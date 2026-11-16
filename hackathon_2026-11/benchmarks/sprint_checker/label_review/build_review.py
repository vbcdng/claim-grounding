"""Card 399 (Sprint K): list the tuning pieces whose label two strong readers dispute and build one review page.

Reads labels only through records.load_labels and answers only through ask.load_answers (tuning part).
Disputed = Sonnet (piece_only answers of card S2 / branch A) and Opus (label_review/opus_tuning) both give a
decisive answer ('supported' or 'not_supported') that is the opposite of the label; or a branch listed the piece in a
label_doubts.jsonl (read-only copies named on the command line); or the piece is one of the 'It' pieces. Never
changes a label. Writes: disputed.json, model_disputed_ids.json, author_ruled_ids.json (empty until the author
answers), review_items.json, and runs decision_page.py for the HTML page.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHARED = HERE.parent / "shared"
REPO = HERE.parents[2]
sys.path.insert(0, str(SHARED))
from sprint_shared_load import sibling  # noqa: E402

records = sibling("records")
ask = sibling("ask")

SONNET = HERE.parent / "branch_a" / "answers" / "sonnet_po_e.jsonl"
OPUS = HERE / "opus_tuning" / "opus_piece_only.jsonl"
DOUBTS = sys.argv[1:]  # label_doubts.jsonl files (read-only copies)
DEC = ("supported", "not_supported")
WORDS = {"supported": "supported", "not_supported": "not supported", "unsure": "unsure"}


def first(answers, pid):
    rows = answers.get(pid) or []
    return rows[0] if rows else None


def norm_find(text, quote):
    if not quote:
        return -1
    q = re.sub(r"\s+", " ", quote).strip()[:80]
    t = re.sub(r"\s+", " ", text)
    return t.find(q), t


def reader_line(name, r):
    if not r or r.get("failed") or not r.get("answer"):
        return "%s: no usable answer." % name
    s = "%s: %s." % (name, WORDS.get(r["answer"], r["answer"]))
    if r.get("quote"):
        s += " Quote: \"%s\"" % r["quote"]
    if r.get("detail"):
        s += " Reason: %s" % r["detail"]
    return s


def main():
    labels = records.load_labels()
    pieces = {p["piece_id"]: p for p in records.load_pieces() if p["piece_id"] in labels}
    son = ask.load_answers(SONNET)
    opu = ask.load_answers(OPUS)
    stats = {"pieces": len(labels), "sonnet_answered": 0, "opus_answered": 0, "opus_failed": 0}
    doubts = {}
    for f in DOUBTS:
        for line in open(f, encoding="utf-8"):
            if line.strip():
                d = json.loads(line)
                if d["piece_id"] in labels:
                    doubts[d["piece_id"]] = d
    both, one_sonnet, one_opus, items_ids = [], [], [], []
    for pid, lab in labels.items():
        s, o = first(son, pid), first(opu, pid)
        sa = s.get("answer") if s and not s.get("failed") else None
        oa = o.get("answer") if o and not o.get("failed") else None
        stats["sonnet_answered"] += sa is not None
        stats["opus_answered"] += oa is not None
        stats["opus_failed"] += (o is None or bool(o.get("failed")))
        sd = sa in DEC and sa != lab["label"]
        od = oa in DEC and oa != lab["label"]
        if sd and od:
            both.append(pid)
        elif sd:
            one_sonnet.append(pid)
        elif od:
            one_opus.append(pid)
    listed = list(dict.fromkeys(both + [p for p in doubts if p not in both]))
    stats.update(both_disagree=len(both), only_sonnet_disagrees=len(one_sonnet), only_opus_disagrees=len(one_opus),
                 branch_doubts=len(doubts), on_page=len(listed))
    # the label sides of the dispute
    stats["both_disagree_label_supported"] = sum(labels[p]["label"] == "supported" for p in both)
    stats["both_disagree_label_not_supported"] = sum(labels[p]["label"] == "not_supported" for p in both)
    (HERE / "model_disputed_ids.json").write_text(json.dumps(listed, indent=1) + "\n", encoding="utf-8")
    if not (HERE / "author_ruled_ids.json").exists():
        (HERE / "author_ruled_ids.json").write_text("[]\n", encoding="utf-8")
    (HERE / "disputed.json").write_text(json.dumps({"stats": stats, "both_disagree": both,
                                                    "only_sonnet": one_sonnet, "only_opus": one_opus,
                                                    "branch_doubts": sorted(doubts)}, indent=1) + "\n", encoding="utf-8")
    items = []
    for i, pid in enumerate(listed, 1):
        p, lab = pieces[pid], labels[pid]
        works = p.get("cited_works") or []
        works = json.loads(works) if isinstance(works, str) else works
        text = ""
        if works and works[0].get("file"):
            text = records.work_text(works[0]["file"])
        ev, quote = "", lab.get("label_quote") or ""
        pos, flat = (-1, "")
        if text:
            res = norm_find(text, quote) if quote else (-1, re.sub(r"\s+", " ", text))
            pos, flat = res
            if pos < 0:
                o = first(opu, pid)
                q2 = (o or {}).get("quote")
                res = norm_find(text, q2) if q2 else (-1, flat)
                pos, flat = res
        if pos >= 0:
            a, b = max(0, pos - 1200), min(len(flat), pos + 1500)
            ev = ("…" if a else "") + flat[a:pos] + "[[ " + flat[pos:pos + 80] + " ]]" + flat[pos + 80:b] + ("…" if b < len(flat) else "")
            ev = "Passage around the quote (the marked words are between [[ and ]]):\n\n" + ev
        elif flat:
            ev = "The quote was not found word for word in the work. The start of the work follows.\n\n" + flat[:2500]
        why = "listed by a branch (card 398)" if pid in doubts and pid not in both else "Sonnet and Opus both disagree with the label"
        ctx = ["Why it is here: " + why,
               "Piece (exact words): " + p["piece"],
               "Label now: %s%s." % (WORDS[lab["label"]], (" (" + lab["label_kind"].replace("_", " ") + ")") if lab.get("label_kind") else ""),
               "Label reason: " + (lab.get("label_reason") or "(none stored)"),
               "Label quote: " + (quote or "(none stored)")]
        if p.get("earlier_sentence"):
            ctx.append("Earlier sentence: " + p["earlier_sentence"])
        ctx += [reader_line("Sonnet", first(son, pid)), reader_line("Opus", first(opu, pid))]
        if pid in doubts:
            ctx.append("Branch note: " + doubts[pid].get("why", ""))
        rec = "keep"
        items.append({
            "id": "k%03d" % i, "title": pid, "context": "\n".join(ctx), "evidence": ev, "full_text": text,
            "checks": [{"id": "k%03d_q" % i, "text": "The label's quote is in the work and reads as the label says."}],
            "options": [{"label": "A", "text": "Label right: keep it."},
                        {"label": "B", "text": "Label wrong: the piece should be '%s' (the key is changed later by the author, not by this card)." % WORDS["supported" if lab["label"] == "not_supported" else "not_supported"]},
                        {"label": "C", "text": "Piece unclear: leave it out of the scoring."}],
            "recommendation": "Read the marked passage first. Keep the label unless the passage clearly says the opposite.",
            "recommended": "A"})
    mins = max(1, round(len(items) * 2))
    intro = ("This page holds %d pieces of the tuning part of the sprint benchmark whose label two strong readers dispute: Sonnet and Opus, "
             "each asked the same neutral question with only the piece and the cited work, both give the opposite answer; "
             "or a branch of the sprint listed the piece. Opus made the original labels, so a dispute is a lower bound on the doubt. "
             "Likely time: about 2 minutes a piece, %d minutes in all. Nothing here changes a label; the answers only fill author_ruled_ids.json. "
             "Choose A, B or C per piece." % (len(items), mins))
    (HERE / "review_items.json").write_text(json.dumps({"title": "Sprint K: labels in doubt (tuning pieces)", "intro": intro,
                                                       "decisions": items}, ensure_ascii=False, indent=1), encoding="utf-8")
    subprocess.run([sys.executable, str(REPO / "benchmarks" / "decision_page.py"), str(HERE / "review_items.json"),
                    "--out", str(HERE / "labels_in_doubt_2026-11-14.html")], check=True)
    print(json.dumps(stats))


if __name__ == "__main__":
    main()
