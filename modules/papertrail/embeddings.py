"""
SPECTER embedding wrapper for local, API-free claim/evidence matching.

Uses the sentence-transformers build of allenai/specter (scientific-text embeddings),
exactly as PaperTrail does. The model is loaded lazily and once per process.

Embeddings for a fixed text list can be cached on disk (embed_cached): encoding is
CPU-bound and dominates re-run wall time, while the vectors depend only on the texts
and the model — so a content-hash-keyed .npz makes re-runs near-free.
"""

import os
import hashlib
import logging
from typing import List

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "sentence-transformers/allenai-specter"

_model = None


def get_model(model_name: str = DEFAULT_MODEL):
    """Lazily load and cache the SPECTER model. The locally-cached copy is tried
    first: without local_files_only the HuggingFace Hub is contacted for
    freshness checks on every load, and those requests stall for tens of seconds
    when the hub throttles unauthenticated clients (2026-07-12 owner terminal
    test). Only a missing/incomplete local copy goes to the network."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        logger.info(f"Loading embedding model: {model_name}")
        try:
            _model = SentenceTransformer(model_name, local_files_only=True)
        except Exception:
            logger.info("No complete local copy — downloading from the HuggingFace Hub "
                        "(one-time, ~440 MB)")
            _model = SentenceTransformer(model_name)
        # A CUDA-build torch on a GPU it has no kernels for still reports
        # cuda-available and only crashes on the first encode
        # (torch.AcceleratorError — 2026-07-14 clean-venv install test, where
        # plain `pip install -r requirements.txt` pulled CUDA torch). Probe
        # once and fall back to CPU: this workload is CPU-sized by design.
        if "cpu" not in str(getattr(_model, "device", "cpu")):
            try:
                _model.encode(["probe"], show_progress_bar=False)
            except Exception as e:
                logger.warning(f"GPU encode failed ({e.__class__.__name__}: {str(e)[:150]}) "
                               f"— falling back to CPU")
                _model = SentenceTransformer(model_name, local_files_only=True, device="cpu")
    return _model


def embed(texts: List[str], model_name: str = DEFAULT_MODEL):
    """Embed a list of texts -> tensor of shape (len(texts), dim)."""
    model = get_model(model_name)
    return model.encode(texts, convert_to_tensor=True, show_progress_bar=False)


def _texts_key(texts: List[str], model_name: str) -> str:
    h = hashlib.sha1(model_name.encode("utf-8"))
    for t in texts:
        h.update(b"\x1f")
        h.update(t.encode("utf-8", "ignore"))
    return h.hexdigest()


def embed_cached(texts: List[str], cache_file: str, model_name: str = DEFAULT_MODEL):
    """
    embed() with a disk cache: vectors are stored as float16 in an .npz keyed by a
    hash of (model, texts). A stale or unreadable cache is recomputed and rewritten;
    cache_file=None degrades to plain embed().
    """
    import numpy as np
    import torch
    key = _texts_key(texts, model_name)
    if cache_file and os.path.exists(cache_file):
        try:
            with np.load(cache_file, allow_pickle=False) as z:
                if str(z["key"]) == key:
                    return torch.from_numpy(z["vecs"].astype("float32"))
        except Exception as e:
            logger.warning(f"Unreadable embedding cache {cache_file} ({e}) — recomputing")
    vecs = embed(texts, model_name)
    if cache_file:
        try:
            os.makedirs(os.path.dirname(cache_file) or ".", exist_ok=True)
            # Atomic write (tmp + rename): a concurrent reader must never see a
            # half-written npz — "the file exists" has to mean "it's complete"
            # (matcher's lock-free steady-state path relies on this).
            tmp = f"{cache_file}.{os.getpid()}.tmp.npz"
            np.savez(tmp, key=key, vecs=vecs.cpu().numpy().astype("float16"))
            os.replace(tmp, cache_file)
        except Exception as e:
            logger.warning(f"Could not write embedding cache {cache_file}: {e}")
    return vecs


def cosine_matrix(a_texts: List[str], b_texts: List[str], model_name: str = DEFAULT_MODEL,
                  a_vecs=None, b_cache_file: str = None):
    """
    Return a cosine-similarity matrix (len(a) x len(b)) as a Python list of lists.
    Empty inputs yield an appropriately-shaped empty matrix.

    a_vecs: pre-computed embeddings for a_texts (skips re-encoding the same texts
    for every source). b_cache_file: disk cache for b_texts' embeddings.
    """
    if not a_texts or not b_texts:
        return [[0.0] * len(b_texts) for _ in a_texts]
    from sentence_transformers import util
    a = a_vecs if a_vecs is not None else embed(a_texts, model_name)
    b = (embed_cached(b_texts, b_cache_file, model_name) if b_cache_file
         else embed(b_texts, model_name))
    sim = util.cos_sim(a, b)  # tensor (len(a), len(b))
    return sim.tolist()
