"""Card 390: check the cut pieces of the 262 benchmark claims and give each piece its door.

Inputs: cut_batches/cut_NN.jsonl (make_cut_batches.py) and cut_out/cut_NN.pieces.jsonl (Sonnet
helpers of the card 390 session, 2026-11-13, subscription, no money; method cut_instructions.md).
Outputs:
- pieces_262.jsonl: one record per piece (record format of the vault page "Pieces the AIDA checker
  is built for"), with the claim's label and row_id kept.
- claim_to_pieces.json: every claim -> its piece ids, entry type, and whether it is lost
  (every piece skipped, or no piece at all).
Door rules (card 389 table with Fable 5.2's changes of 13 Nov, D1848):
- skipped: the writer's own statement (attribution settled), whatever its kind; a pointer or an
  announcement is skipped ONLY when it is the writer's own (Fable change 2);
- tagged: attribution open, needs arithmetic, leans on an earlier sentence with no fill-in,
  two readings, planted;
- in scope: everything else (credited; credited opinions included).
Out-of-scope parts are dropped as pieces after cutting, never as whole claims (Fable change 3).
Run: ./venv/bin/python3 benchmarks/card390/merge_cuts.py
"""
import collections
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
from modules.papertrail.aida import record as rec  # noqa: E402

BENCH = ROOT / "data" / "card191_verdict_bench_9b" / "expected.json"
BATCHES = HERE / "cut_batches"
CUTS = HERE / "cut_out"
CUTTER = "Sonnet (claude-sonnet-5-5) through the subscription, helpers of the card 390 session, 2026-11-13"
KINDS = ("fact", "opinion", "pointer", "announcement")
CERTAINTY = ("stated", "likely", "possible")
ATTR = ("credited_to_cited_source", "stated_by_writer")
TAG_ORDER = ("several_works", "needs_arithmetic", "no_standalone_sentence", "attribution_open",
             "two_readings", "planted", "leans_filled")
TAGGED = {"needs_arithmetic", "no_standalone_sentence", "attribution_open", "two_readings", "planted"}


def read_jsonl(path):
    return [json.loads(l) for l in open(path) if l.strip()]


def norm(s):
    return re.sub(r"\s+", " ", s.replace(" ", " ")).strip()


def is_planted(row):
    return row["origin_id"].startswith("negated/")


def piece_tags(row, entry, p):
    tags = []
    if len(row["sources"]) > 1:
        tags.append("several_works")
    if p.get("needs_computation"):
        tags.append("needs_arithmetic")
    if p.get("leans_on_earlier"):
        tags.append("leans_filled" if (p.get("filled_from_before") or "").strip()
                    else "no_standalone_sentence")
    if p.get("attribution_open"):
        tags.append("attribution_open")
    if entry["entry_type"] == "ambiguous":
        tags.append("two_readings")
    if is_planted(row):
        tags.append("planted")
    return [t for t in TAG_ORDER if t in tags]


def door(p, tags):
    """skipped / tagged / in_scope for one piece."""
    own = p["attribution"] == "stated_by_writer" and not p.get("attribution_open")
    if own:
        return "skipped"
    if set(tags) & TAGGED:
        return "tagged"
    return "in_scope"


def check_piece(p, claim, problems, pid):
    for k in ("text", "certainty", "attribution", "kind", "covers"):
        if k not in p:
            problems.append((pid, "missing field " + k))
    if p.get("kind") not in KINDS:
        problems.append((pid, "bad kind %r" % p.get("kind")))
    if p.get("certainty") not in CERTAINTY:
        problems.append((pid, "bad certainty %r" % p.get("certainty")))
    if p.get("attribution") not in ATTR:
        problems.append((pid, "bad attribution %r" % p.get("attribution")))
    for c in p.get("covers") or []:
        if norm(c) not in norm(claim):
            problems.append((pid, "cover not in claim: %r" % c[:60]))


def form_warnings(p):
    """Mechanical form checks of the key's validator (rules A2, A3): warnings, never a refusal."""
    w = []
    if not p.get("leans_on_earlier"):
        pw = rec.pointer_words_in(p["text"])
        if pw:
            w.append("pointer words " + ",".join(pw))
    for sev, level, cue, _ in rec.hedge_findings(p["text"]):
        if sev == "error":
            w.append("hedge cue left: " + cue)
    return w


def build():
    rows = {r["row_id"]: r for r in json.load(open(BENCH))["rows"]}
    pieces, claims, problems = [], {}, []
    for b in sorted(BATCHES.glob("cut_*.jsonl")):
        ins = read_jsonl(b)
        outs = read_jsonl(CUTS / (b.stem + ".pieces.jsonl"))
        if [o["row_id"] for o in outs] != [i["row_id"] for i in ins]:
            raise SystemExit("%s: output row ids differ from input" % b.name)
        for i, entry in zip(ins, outs):
            row = rows[i["row_id"]]
            ids = []
            readings = entry.get("readings") or []
            for ri, rd in enumerate(readings):
                for pi, p in enumerate(rd.get("pieces") or []):
                    pid = "%s.r%d.p%d" % (row["row_id"], ri, pi)
                    check_piece(p, row["claim_text"], problems, pid)
                    tags = piece_tags(row, entry, p)
                    d = door(p, tags)
                    pieces.append({
                        "piece_id": pid, "row_id": row["row_id"], "label": row["label"],
                        "label_kind": row.get("kind"), "piece": p["text"],
                        "certainty": p["certainty"], "certainty_cue": p.get("certainty_cue", ""),
                        "attribution": ("open" if p.get("attribution_open") else
                                        "credited" if p["attribution"] == ATTR[0] else "writer_own"),
                        "checkable": "no" if d == "skipped" else "yes",
                        "kind": p["kind"], "tags": tags, "door": d,
                        "cited_key": p.get("cited_key", ""),
                        "cited_works": [s["name"] for s in row["sources"]],
                        "needs_computation": p.get("needs_computation"),
                        "reading_label": rd.get("label", ""),
                        "original_sentence": row["claim_text"],
                        "earlier_sentence": i["text_before"] if p.get("leans_on_earlier") else "",
                        "filled_from_before": p.get("filled_from_before", ""),
                        "covers": p.get("covers", []),
                        "form_warnings": form_warnings(p),
                        "cut_by": CUTTER})
                    ids.append(pid)
            doors = [x["door"] for x in pieces[len(pieces) - len(ids):]]
            claims[row["row_id"]] = {
                "label": row["label"], "label_kind": row.get("kind"),
                "entry_type": entry["entry_type"], "reason": entry.get("reason", ""),
                "piece_ids": ids, "doors": dict(collections.Counter(doors)),
                "lost": not any(d != "skipped" for d in doors)}
    if sorted(claims) != sorted(rows):
        raise SystemExit("cut claims do not cover the 262 benchmark claims exactly")
    return pieces, claims, problems


def summary(pieces, claims):
    out = collections.OrderedDict()
    out["claims"] = len(claims)
    out["pieces"] = len(pieces)
    out["entry_types"] = dict(collections.Counter(c["entry_type"] for c in claims.values()))
    out["piece_doors"] = dict(collections.Counter(p["door"] for p in pieces))
    out["piece_doors_by_label"] = {"%s/%s" % k: v for k, v in sorted(
        collections.Counter((p["label"], p["door"]) for p in pieces).items())}
    out["piece_kinds"] = dict(collections.Counter(p["kind"] for p in pieces))
    out["piece_tags"] = dict(collections.Counter(t for p in pieces for t in p["tags"]))
    lost = [k for k, c in claims.items() if c["lost"]]
    out["claims_lost"] = len(lost)
    out["claims_lost_by_label"] = dict(collections.Counter(claims[k]["label"] for k in lost))
    out["wrong_claims_lost"] = sorted(k for k in lost if claims[k]["label"] == "not_supported")
    out["pieces_with_form_warnings"] = sum(1 for p in pieces if p["form_warnings"])
    return out


def main():
    pieces, claims, problems = build()
    if problems:
        for pid, msg in problems:
            print("PROBLEM", pid, msg)
        raise SystemExit("%d problems" % len(problems))
    with open(HERE / "pieces_262.jsonl", "w") as f:
        for p in pieces:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    s = summary(pieces, claims)
    json.dump({"what": "card 390: each of the 262 benchmark claims mapped to its pieces "
                       "(pieces_262.jsonl); 'lost' = every piece skipped or no piece",
               "cut_by": CUTTER, "summary": s, "claims": claims},
              open(HERE / "claim_to_pieces.json", "w"), indent=1, ensure_ascii=False)
    print(json.dumps(s, indent=1))


if __name__ == "__main__":
    main()
