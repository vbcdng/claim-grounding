"""Card S1 (card 395): score a decisions file on the tuning part (or, with --final, the held-back part).

A decisions file is jsonl, one line per piece: piece_id, answer (supported / not_supported /
undecided / not_checked / skipped), step (the step that settled it), and optionally cost_usd and
tokens ({step: input tokens}). combine.py writes it.

Three numbers, always together, each with its 95% range (Wilson, as card191_verdict_bench.py)
and the count of claims the pieces come from:
- wrongly passed: wrong pieces answered "supported", of all wrong pieces of the group;
- wrongly rejected: right pieces answered "not supported", of all right pieces of the group;
- left undecided: "undecided" or "not_checked", of all pieces of the group.
Groups: the main group (pieces in scope), all checked pieces (in scope and tagged), each separate
group and each tag, the three field groups (certainty possible or likely; an earlier sentence
stored; all others), wrongly passed by kind of error, and count, dollars and tokens per step.
A skipped piece (the writer's own) is in no group. A piece of the part with no decision counts as
undecided, and the count is printed.

--without FILE prints the same without the listed piece ids. --final prints the held-back part,
and every such use is written to final_uses.log (date, user, folder, command, calling process).
"""
import argparse
import datetime
import getpass
import json
import math
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from sprint_shared_load import sibling  # noqa: E402

records = sibling("records")
split = sibling("split")

FINAL_LOG = HERE / "final_uses.log"
GOAL = 0.05
UNUSABLE_UNDECIDED = 0.20
UNDECIDED = ("undecided", "not_checked")


def wilson(k, n, z=1.96):
    """card191_verdict_bench.wilson, copied (importing that module pulls its whole benchmark)."""
    if n == 0:
        return (None, None)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(max(0.0, c - h), 3), round(min(1.0, c + h), 3))


def read_decisions(path):
    out = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                d = json.loads(line)
                out[d["piece_id"]] = d
    return out


def read_ids(path):
    text = Path(path).read_text(encoding="utf-8").strip()
    return set(json.loads(text) if text.startswith("[") else text.split())


def log_final(argv, path=None):
    path = path or FINAL_LOG
    try:
        with open(f"/proc/{os.getppid()}/cmdline", "rb") as f:
            parent = f.read().replace(b"\0", b" ").decode(errors="replace").strip()
    except OSError:
        parent = ""
    rec = {"at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
           "user": getpass.getuser(), "cwd": os.getcwd(), "argv": list(argv), "parent": parent[:300]}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def rates(ids, labels, decisions):
    """The three numbers for a set of piece ids."""
    wrong = [p for p in ids if labels[p]["label"] == "not_supported"]
    right = [p for p in ids if labels[p]["label"] == "supported"]
    ans = {p: (decisions.get(p) or {}).get("answer", "undecided") for p in ids}
    wp = [p for p in wrong if ans[p] == "supported"]
    wr = [p for p in right if ans[p] == "not_supported"]
    und = [p for p in ids if ans[p] in UNDECIDED]
    claims = lambda ps: len({labels[p]["row_id"] for p in ps})  # noqa: E731
    return {"n": len(ids), "claims": claims(ids),
            "wrongly_passed": (len(wp), len(wrong), claims(wp), claims(wrong)),
            "wrongly_rejected": (len(wr), len(right), claims(wr), claims(right)),
            "undecided": (len(und), len(ids), claims(und), claims(ids)),
            "not_checked": sum(1 for p in ids if ans[p] == "not_checked")}


def fmt(t):
    k, n, ck, cn = t
    if n == 0:
        return "0 of 0"
    lo, hi = wilson(k, n)
    return f"{k} of {n} ({100 * k / n:.1f} in 100; range {100 * lo:.1f} to {100 * hi:.1f}; claims {ck} of {cn})"


def goal_word(t):
    k, n = t[0], t[1]
    if n == 0:
        return "no pieces"
    lo, hi = wilson(k, n)
    if hi < GOAL:
        return "under 5 in 100: shown"
    if lo >= GOAL:
        return "under 5 in 100: not reached"
    return "under 5 in 100: not decidable with this many pieces"


def groups(labels, ids):
    g = defaultdict(list)
    for p in ids:
        lab = labels[p]
        if lab.get("attribution") == records.WRITER_OWN:
            continue
        if lab["door"] == "in_scope":
            g["main group (pieces in scope)"].append(p)
        g["all checked pieces"].append(p)
        for s in lab.get("separate_groups") or []:
            g[f"separate group: {s}"].append(p)
        for t in lab.get("tags") or []:
            g[f"tag: {t}"].append(p)
        if lab.get("certainty") in split.HEDGED:
            g["field group: certainty possible or likely"].append(p)
        elif lab.get("earlier_sentence"):
            g["field group: earlier sentence stored"].append(p)
        else:
            g["field group: all others"].append(p)
        if lab.get("certainty") in split.HEDGED and lab.get("earlier_sentence"):
            g["field group: earlier sentence stored"].append(p)
    return g


def report(decisions, labels, ids, title):
    lines = [f"== {title}: {len(ids)} pieces from {len({labels[p]['row_id'] for p in ids})} claims"]
    missing = [p for p in ids if p not in decisions]
    if missing:
        lines.append(f"   {len(missing)} pieces of the part have no decision; counted as undecided")
    skipped = [p for p in ids if (decisions.get(p) or {}).get("answer") == "skipped"]
    if skipped:
        lines.append(f"   {len(skipped)} pieces skipped as the writer's own; in no group")
    g = groups(labels, [p for p in ids if p not in skipped])
    order = ["main group (pieces in scope)", "all checked pieces"] + sorted(k for k in g if k.startswith("field")) \
        + sorted(k for k in g if k.startswith(("separate", "tag")))
    for name in order:
        if name not in g:
            continue
        r = rates(g[name], labels, decisions)
        u = r["undecided"]
        warn = "  NOT USABLE YET (over 20 in 100 undecided)" if u[1] and u[0] / u[1] > UNUSABLE_UNDECIDED else ""
        lines.append(f"-- {name}: {r['n']} pieces, {r['claims']} claims{warn}")
        lines.append(f"   wrongly passed   {fmt(r['wrongly_passed'])}")
        lines.append(f"   wrongly rejected {fmt(r['wrongly_rejected'])}")
        lines.append(f"   left undecided   {fmt(u)}; of these not checked (failed calls) {r['not_checked']}")
        if name.startswith(("main", "all checked")):
            lines.append(f"   goal: wrongly passed {goal_word(r['wrongly_passed'])}; "
                         f"wrongly rejected {goal_word(r['wrongly_rejected'])}")
    main = g.get("all checked pieces", [])
    kinds = Counter(labels[p]["label_kind"] for p in main if labels[p]["label"] == "not_supported")
    lines.append("-- wrongly passed by kind of error (all checked pieces)")
    for kind in sorted(kinds):
        ps = [p for p in main if labels[p]["label"] == "not_supported" and labels[p]["label_kind"] == kind]
        wp = [p for p in ps if (decisions.get(p) or {}).get("answer") == "supported"]
        lines.append(f"   {kind}: {fmt((len(wp), len(ps), len({labels[p]['row_id'] for p in wp}), len({labels[p]['row_id'] for p in ps})))}")
    lines.append("-- per step: pieces settled, dollars, input tokens (of all pieces of the part)")
    per = defaultdict(lambda: [0, 0.0])
    toks = Counter()
    usd_steps = Counter()
    for p in ids:
        d = decisions.get(p) or {"step": "no decision"}
        per[d.get("step") or "?"][0] += 1
        per[d.get("step") or "?"][1] += float(d.get("cost_usd") or 0.0)
        for s, t in (d.get("tokens") or {}).items():
            toks[s] += int(t or 0)
        for s, c in (d.get("cost_by_step") or {}).items():
            usd_steps[s] += float(c or 0.0)
    for s in sorted(per):
        lines.append(f"   settled by {s}: {per[s][0]} pieces; ${per[s][1]:.4f} for these pieces")
    for s in sorted(set(toks) | set(usd_steps)):
        lines.append(f"   step {s} was asked: {toks[s]:,} input tokens, ${usd_steps[s]:.4f}")
    return "\n".join(lines)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    ap = argparse.ArgumentParser(description="Card S1: score a decisions file. Tuning part only, unless --final "
                                             "(held-back part; every use is logged).")
    ap.add_argument("decisions", help="decisions file (jsonl) written by combine.py")
    ap.add_argument("--without", help="file of piece ids (one per line, or a JSON list) to leave out")
    ap.add_argument("--pieces", help="file of piece ids: score only these (within the part)")
    ap.add_argument("--final", action="store_true", help="score the held-back part; written to final_uses.log")
    ap.add_argument("--split", default=str(split.SPLIT))
    a = ap.parse_args(argv)
    if a.final:
        log_final(["score.py"] + list(argv))
    labels = records.load_labels(final=a.final, split_path=a.split)
    part = "held_back" if a.final else "tuning"
    ids = sorted(set(split.load_split(a.split)[part]["pieces"]) & set(labels))
    title = "held-back part (--final)" if a.final else "tuning part"
    if a.pieces:
        keep = read_ids(a.pieces)
        ids = [p for p in ids if p in keep]
        title += f", only the {len(ids)} listed pieces of this part"
    if a.without:
        drop = read_ids(a.without)
        ids = [p for p in ids if p not in drop]
        title += f", without {len(drop)} listed pieces"
    print(report(read_decisions(a.decisions), labels, ids, title))
    return 0


if __name__ == "__main__":
    sys.exit(main())
