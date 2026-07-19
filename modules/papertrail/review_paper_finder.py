"""Stream D — close the review loop for 'wrong source' claims.

When the author marks a claim ``wrong_source`` in the viewer, its cited source
does not actually establish it. This module takes those marks, searches for a
better paper (Stream B's ``paper_search``), downloads the top open-access
candidate (the shared ``direct_downloader``), and REGISTERS it in the project's
``sources_manifest.json`` + ``*.refs.txt`` so the next ``verify_my_text.py`` run
can use it.

It is deliberately **propose-only**: it fetches and registers candidate sources
and prints which ``[[key]]`` to cite for each claim, but it NEVER edits the
author's text or decides the citation for them (owner rule: the user stays in
control of every edit). Citing the new key stays a human/``/apply-review`` step.

``find_replacements`` takes injectable ``search_fn`` / ``download_fn`` so the
whole flow is unit-testable offline; the CLI (``find_replacement_sources.py``)
wires the real Stream-B search + OA downloader.
"""

import json
import os
import re
import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

WRONG_SOURCE = "wrong_source"


# ---------------------------------------------------------------- helpers

def _wrong_source_marks(review: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The marked claims whose author action includes 'wrong_source'."""
    out = []
    for m in review.get("marks", []) or []:
        if WRONG_SOURCE in (m.get("marks") or []):
            out.append(m)
    return out


def _find_refs(project_dir: str) -> Optional[str]:
    """The project's *.refs.txt (the [[key]] -> filename map). None if absent."""
    try:
        names = sorted(n for n in os.listdir(project_dir) if n.endswith(".refs.txt"))
    except OSError:
        return None
    return os.path.join(project_dir, names[0]) if names else None


def _load_json(path: Optional[str]) -> Optional[Dict[str, Any]]:
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        logger.warning(f"Could not read {path}: {e}")
        return None


def _dump_json(path: str, obj: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)


def _slug_key(title: Optional[str], year: Any, taken: set) -> str:
    """A citation key from the title's first real word + year, uniquified.
    Mirrors the importer's key style closely enough to read naturally."""
    words = re.findall(r"[A-Za-z]+", (title or "").lower())
    stop = {"the", "a", "an", "on", "of", "in", "and", "for", "to", "is", "are"}
    head = next((w for w in words if w not in stop and len(w) > 2), "source")
    yr = re.sub(r"[^0-9]", "", str(year or ""))[:4]
    base = f"{head}{yr}" if yr else head
    key, n = base, 2
    while key in taken:
        key = f"{base}_{n}"
        n += 1
    return key


def _refs_keys(refs_path: Optional[str]) -> set:
    """Keys already bound in the refs file. A slug collision with one of these
    would make _add_refs_line REWRITE the author's live `key = file` mapping —
    every [[key]] citation in their text would silently resolve to the new,
    different paper on the next verify run."""
    if not refs_path or not os.path.exists(refs_path):
        return set()
    keys = set()
    with open(refs_path, encoding="utf-8") as f:
        for line in f:
            m = re.match(r"\s*([^=#\s][^=]*?)\s*=", line)
            if m:
                keys.add(m.group(1).strip())
    return keys


def _add_refs_line(refs_path: Optional[str], key: str, filename: str) -> None:
    """Append/replace the `key = filename` line in the refs file (idempotent)."""
    if not refs_path:
        return
    line = f"{key} = {filename}\n"
    existing = ""
    if os.path.exists(refs_path):
        with open(refs_path, encoding="utf-8") as f:
            existing = f.read()
    # Replace an existing mapping for this key, else append.
    pat = re.compile(rf"^{re.escape(key)}\s*=.*$", re.MULTILINE)
    if pat.search(existing):
        existing = pat.sub(line.rstrip("\n"), existing)
        if not existing.endswith("\n"):
            existing += "\n"
    else:
        if existing and not existing.endswith("\n"):
            existing += "\n"
        existing += line
    with open(refs_path, "w", encoding="utf-8") as f:
        f.write(existing)


def _register(manifest: Dict[str, Any], refs_path: Optional[str], key: str,
              filename: str, title: Optional[str], doi: Optional[str],
              url: Optional[str], year: Any) -> None:
    """Record a downloaded source in the manifest (dedup by key) + refs file."""
    srcs = manifest.setdefault("sources", [])
    if not any(s.get("key") == key for s in srcs):
        srcs.append({
            "key": key, "title": title, "author": None, "year": str(year or ""),
            "url": url, "doi": doi, "suggested_filename": filename,
            "status": "has_link", "added_by": "review_paper_finder",
        })
    _add_refs_line(refs_path, key, filename)


def _default_search(claim_text: str, keywords: Any, llm=None, cache_dir=None):
    from modules.papertrail import paper_search
    return paper_search.snowball(claim_text, keywords, llm=llm, cache_dir=cache_dir)


def _default_download(entry: Dict[str, Any], sources_dir: str, session):
    from modules.papertrail import direct_downloader as dd
    return dd.download_source(dd.normalize_entry(entry), sources_dir, session)


# A download outcome is "usable" if a file with real text landed.
_USABLE = {"already_present", "pdf", "text"}


# ---------------------------------------------------------------- core

def find_replacements(review: Dict[str, Any], project_dir: str, *,
                      top_k: int = 3, download: bool = True, llm=None,
                      search_fn: Optional[Callable] = None,
                      download_fn: Optional[Callable] = None,
                      session: Any = None, cache_dir: Optional[str] = None
                      ) -> Dict[str, Any]:
    """For every 'wrong_source' claim in ``review``, search for a better paper,
    (optionally) download the top open-access candidates, register the usable
    ones, and return a propose-only report. Never edits the author's text."""
    search_fn = search_fn or _default_search
    sources_dir = os.path.join(project_dir, "sources")
    manifest_path = os.path.join(project_dir, "sources_manifest.json")
    refs_path = _find_refs(project_dir)
    manifest = _load_json(manifest_path) or {"sources": []}
    taken = {s.get("key") for s in manifest.get("sources", []) if s.get("key")}
    taken |= _refs_keys(refs_path)   # refs-only keys count too (manual/ingested)

    if download:
        os.makedirs(sources_dir, exist_ok=True)

    proposals = []
    for claim in _wrong_source_marks(review):
        res = search_fn(claim.get("text", ""), claim.get("text", ""),
                        llm=llm, cache_dir=cache_dir) or {}
        # Only candidates we can actually fetch (have a DOI or URL).
        cands = [c for c in (res.get("candidates") or [])
                 if c.get("doi") or c.get("url")][:top_k]
        cand_reports = []
        for c in cands:
            key = _slug_key(c.get("title"), c.get("year"), taken)
            taken.add(key)  # reserve even if download fails, to avoid collisions
            cand_reports.append({"key": key, "title": c.get("title"),
                                 "doi": c.get("doi"), "url": c.get("url"),
                                 "relevance": c.get("relevance"),
                                 "reason": c.get("reason"), "downloaded": False,
                                 "filename": None, "outcome": None, "_cand": c})
        if download and cand_reports:
            dlfn = download_fn or _default_download

            def fetch(rec):
                entry = {"key": rec["key"], "title": rec["title"],
                         "year": rec["_cand"].get("year"), "doi": rec["doi"],
                         "url": rec["url"],
                         "suggested_filename": f"{rec['key']}.pdf"}
                try:                     # a bad fetch must not sink the whole loop
                    return dlfn(entry, sources_dir, session) or {}
                except Exception as e:
                    return {"outcome": f"error: {e}"}

            # Downloads are independent network fetches -> parallel; the
            # registration below stays serial (manifest/refs/taken are shared).
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=min(4, len(cand_reports))) as ex:
                results = list(ex.map(fetch, cand_reports))
            for rec, r in zip(cand_reports, results):
                rec["outcome"] = r.get("outcome")
                rec["filename"] = r.get("filename")
                if r.get("outcome") in _USABLE and r.get("filename"):
                    rec["downloaded"] = True
                    c = rec["_cand"]
                    _register(manifest, refs_path, rec["key"], r["filename"],
                              c.get("title"), c.get("doi"), c.get("url"),
                              c.get("year"))
        for rec in cand_reports:
            rec.pop("_cand", None)
        proposals.append({
            "claim_id": claim.get("id"),
            "claim_text": claim.get("text"),
            "current_markers": claim.get("markers"),
            "search_status": res.get("status"),
            "candidates": cand_reports,
            # The best fetched candidate to consider citing — a SUGGESTION only.
            "suggested_key": next((r["key"] for r in cand_reports if r["downloaded"]),
                                  None),
        })

    if download:
        _dump_json(manifest_path, manifest)

    return {"project_dir": project_dir, "refs_file": refs_path,
            "n_claims": len(proposals), "downloaded_any": download,
            "proposals": proposals}


def render_report(report: Dict[str, Any]) -> str:
    """Human-readable markdown proposal — what to consider citing, never applied."""
    lines = ["# Replacement-source proposals (Stream D)", "",
             "Propose-only: candidate papers were searched"
             + (" and downloaded" if report.get("downloaded_any") else "")
             + " for each claim you marked **wrong source**. "
               "Nothing in your text was changed — cite a `[[key]]` yourself "
               "(or via `/apply-review`) after checking it establishes the claim.",
             ""]
    if not report["proposals"]:
        lines.append("_No claims marked `wrong_source` in this review._")
        return "\n".join(lines) + "\n"
    for p in report["proposals"]:
        cur = ", ".join(f"[[{m}]]" for m in (p.get("current_markers") or [])) or "—"
        lines += [f"## {p['claim_id']} (currently cites {cur})",
                  f"> {p.get('claim_text','')}",
                  f"\nsearch: `{p.get('search_status')}`"]
        sug = p.get("suggested_key")
        if sug:
            lines.append(f"\n**Suggested:** cite `[[{sug}]]` (downloaded, ready to verify).")
        if not p["candidates"]:
            lines.append("\n_No fetchable candidates found._")
        for r in p["candidates"]:
            mark = "✓ downloaded" if r["downloaded"] else f"· {r.get('outcome') or 'not fetched'}"
            rel = f" · relevance {r['relevance']:.2f}" if isinstance(r.get("relevance"), (int, float)) else ""
            doi = f" · doi:{r['doi']}" if r.get("doi") else (f" · {r['url']}" if r.get("url") else "")
            lines.append(f"- `[[{r['key']}]]` {mark}{rel} — {r.get('title') or '(untitled)'}{doi}")
        lines.append("")
    return "\n".join(lines) + "\n"
