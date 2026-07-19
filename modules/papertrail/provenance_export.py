"""
Slot B4: export a finished run's analysis.json as a nanopublication-ISOMORPHIC
provenance file (plain JSON — imitate the shape, don't import the RDF stack;
IDEAS.md "Interoperable output format", docs/submission/EPISTACK_LANDSCAPE.md §6).

Each verdict becomes one record with the nanopub three-graph split:
  assertion        — the claim text + our verdict (what is being said)
  provenance       — PROV-O-named links: how the assertion was derived
                     (prov:wasDerivedFrom sources, prov:wasGeneratedBy the
                     judging activity, prov:used evidence quotes as W3C Web
                     Annotation-style targets)
  publication_info — run metadata shared by every record (tool, model, date)

This buys a mechanical future RDF/nanopub (TriG) export path without making the
day-to-day pipeline carry RDF. The viewer + analysis.json stay the primary
artifacts; this file is the interop surface ("artifacts must compound").

Standalone use (no LLM, no network):
  python3 -m modules.papertrail.provenance_export <run-dir> [--include-omitted N]
writes <run-dir>/provenance.json. See docs/PROVENANCE_FORMAT.md for the mapping.
"""

import os
import json
import hashlib
import argparse
from typing import Dict, Any, List, Optional

TOOL = "claim-grounding/verify_my_text"
FORMAT_VERSION = 1
# Compact JSON-LD-style context: OUR key -> the standard term it is isomorphic to.
CONTEXT = {
    "assertion": "np:assertion",
    "provenance": "np:provenance",
    "publication_info": "np:pubinfo",
    "derived_from": "prov:wasDerivedFrom",
    "generated_by": "prov:wasGeneratedBy",
    "attributed_to": "prov:wasAttributedTo",
    "used": "prov:used",
    "quoted_from": "prov:wasQuotedFrom",
    "exact": "oa:exact",
    "target": "oa:hasTarget",
    "source": "oa:hasSource",
    "np": "http://www.nanopub.org/nschema#",
    "prov": "http://www.w3.org/ns/prov#",
    "oa": "http://www.w3.org/ns/oa#",
}
DEFAULT_OMITTED = 15    # the viewer's own cap; 28k+ raw omitted rows would drown interop


def _run_id(metadata: Dict[str, Any]) -> str:
    basis = f"{metadata.get('text_file','')}|{metadata.get('model','')}|{metadata.get('timestamp','')}"
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:12]


def _source_entities(analysis: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    hashes = (analysis.get("metadata") or {}).get("source_hashes") or {}
    out = {}
    for s in analysis.get("sources", []):
        out[s["paper_id"]] = {
            "id": f"source:{s['paper_id']}",
            "title": s.get("title"),
            "filename": s.get("filename"),
            "citation_key": s.get("key"),
            "content_sha1": hashes.get(s.get("key")) or hashes.get(s.get("filename")),
        }
    return out


def _evidence_use(e: Dict[str, Any]) -> Dict[str, Any]:
    """One judged evidence entry -> a prov:used record with a Web-Annotation-style
    target (source + page + the exact quoted sentence)."""
    use = {
        "quoted_from": f"source:{e.get('paper_id')}",
        "supported": e.get("supported"),
        "reason": e.get("reason"),
    }
    if e.get("sentence"):
        use["target"] = {
            "source": f"source:{e.get('paper_id')}",
            "page": e.get("page"),
            "exact": e.get("sentence"),
        }
    if e.get("cosine") is not None:
        use["retrieval_cosine"] = e.get("cosine")
    return {k: v for k, v in use.items() if v is not None}


def _claim_record(c: Dict[str, Any], run_id: str) -> Dict[str, Any]:
    assertion: Dict[str, Any] = {"text": c.get("text"), "verdict": c.get("verdict")}
    for flag in ("partial_support", "over_citation", "own_kind", "second_opinion"):
        if c.get(flag):
            assertion[flag] = c[flag]
    activity: Dict[str, Any] = {
        "activity": "citation-support-judgment",
        "method": c.get("method"),
        "reason": c.get("reason"),
    }
    if c.get("votes"):
        activity["votes"] = c["votes"]
    provenance: Dict[str, Any] = {
        "derived_from": [f"source:{pid}" for pid in (c.get("paper_ids") or [])],
        "generated_by": {k: v for k, v in activity.items() if v is not None},
        "used": [_evidence_use(e) for e in (c.get("evidences") or []) if e],
    }
    if not provenance["derived_from"]:
        # an uncited (own) claim is attributed to the author, derived from nothing
        provenance = {"attributed_to": "author", "generated_by": provenance["generated_by"]}
    return {"id": f"urn:pt:{run_id}:{c.get('id')}", "assertion": assertion,
            "provenance": provenance}


def _omitted_record(o: Dict[str, Any], run_id: str, rank: int) -> Dict[str, Any]:
    return {
        "id": f"urn:pt:{run_id}:omitted:{o.get('source_claim_id', rank)}",
        "assertion": {"text": o.get("text"), "verdict": "omitted"},
        "provenance": {
            "attributed_to": f"source:{o.get('paper_id')}",
            "generated_by": {"activity": "omitted-claim-ranking",
                             "relevance_cosine": o.get("relevance")},
            **({"used": [{"quoted_from": f"source:{o.get('paper_id')}",
                          "target": {"source": f"source:{o.get('paper_id')}",
                                     "page": o.get("page"),
                                     "exact": o.get("evidence")}}]}
               if o.get("evidence") else {}),
        },
    }


def export(analysis: Dict[str, Any], include_omitted: int = DEFAULT_OMITTED) -> Dict[str, Any]:
    """analysis.json dict -> nanopub-isomorphic provenance dict (pure function)."""
    md = analysis.get("metadata") or {}
    run_id = _run_id(md)
    records = [_claim_record(c, run_id) for c in analysis.get("text_claims", [])]
    omitted = sorted(analysis.get("omitted", []),
                     key=lambda o: -(o.get("relevance") or 0))[:max(include_omitted, 0)]
    records += [_omitted_record(o, run_id, i) for i, o in enumerate(omitted)]
    return {
        "@context": CONTEXT,
        "format": "papertrail-provenance",
        "format_version": FORMAT_VERSION,
        "publication_info": {
            "run_id": run_id,
            "generated_by_tool": TOOL,
            "model": md.get("model"),
            "created": md.get("timestamp"),
            "text_file": os.path.basename(md.get("text_file") or "") or None,
            "n_records": len(records),
            "omitted_included": len(omitted),
            "omitted_total": len(analysis.get("omitted", [])),
        },
        "sources": list(_source_entities(analysis).values()),
        "records": records,
    }


def export_file(run_dir: str, include_omitted: int = DEFAULT_OMITTED,
                out_path: Optional[str] = None) -> str:
    with open(os.path.join(run_dir, "analysis.json"), encoding="utf-8") as f:
        analysis = json.load(f)
    out = out_path or os.path.join(run_dir, "provenance.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(export(analysis, include_omitted), f, indent=2, ensure_ascii=False)
    return out


def main():
    ap = argparse.ArgumentParser(description="Export a run's provenance.json "
                                             "(nanopub-isomorphic, PROV-O terms).")
    ap.add_argument("run_dir", help="a finished run's --output-dir (holds analysis.json)")
    ap.add_argument("--include-omitted", type=int, default=DEFAULT_OMITTED,
                    help=f"top-N omitted source claims by relevance (default {DEFAULT_OMITTED})")
    ap.add_argument("--out", default=None, help="output path (default <run-dir>/provenance.json)")
    args = ap.parse_args()
    print(export_file(args.run_dir, args.include_omitted, args.out))


if __name__ == "__main__":
    main()
