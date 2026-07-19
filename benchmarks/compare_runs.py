# Compare two analysis.json snapshots claim-by-claim — no API calls.
# Usage: venv/bin/python3 benchmarks/compare_runs.py <old.json> <new.json>
# Prints verdict flips (with evidence sentence + reason), totals, and timing,
# so a tuning change's effect on a re-run is visible in one screen instead of
# hand-diffing viewer.html. See docs/PAPER1_TUNING_STATE.md for the paper1
# ground truth to judge flips against.
import sys
import json


def load(path):
    with open(path, "r", encoding="utf-8") as f:
        a = json.load(f)
    return {c["id"]: c for c in a["text_claims"]}, a


def brief(c, n=90):
    e = c.get("evidence") or {}
    sent = (e.get("sentence") or "")[:n]
    reason = (c.get("reason") or e.get("reason") or "")[:n]
    return sent, reason


def main():
    old_path, new_path = sys.argv[1], sys.argv[2]
    old, a_old = load(old_path)
    new, a_new = load(new_path)

    flips, changed_evidence = [], []
    for cid, nc in new.items():
        oc = old.get(cid)
        if not oc:
            continue
        if oc["verdict"] != nc["verdict"]:
            flips.append((cid, oc["verdict"], nc["verdict"], nc))
        elif nc["verdict"] == "supported":
            os_, _ = brief(oc)
            ns_, _ = brief(nc)
            if os_ != ns_:
                changed_evidence.append((cid, os_, ns_))

    def totals(a):
        t = a["coverage"]["totals"]
        return f"{t['supported']} supported / {t['unsupported']} unsupported / {t['omitted']} omitted"

    print(f"OLD {old_path}: {totals(a_old)} "
          f"({a_old['metadata'].get('processing_time_seconds', '?')}s)")
    print(f"NEW {new_path}: {totals(a_new)} "
          f"({a_new['metadata'].get('processing_time_seconds', '?')}s)")

    print(f"\nVerdict flips ({len(flips)}):")
    for cid, ov, nv, nc in sorted(flips, key=lambda x: int(x[0][1:]) if x[0][1:].isdigit() else 0):
        sent, reason = brief(nc)
        arrow = "GAINED" if nv == "supported" else "LOST  "
        print(f"  {arrow} {cid}: {ov} -> {nv}")
        print(f"         claim:    {nc['text'][:90]}")
        if sent:
            print(f"         evidence: {sent}")
        if reason:
            print(f"         reason:   {reason}")

    if changed_evidence:
        print(f"\nSame verdict, different supporting sentence ({len(changed_evidence)}):")
        for cid, os_, ns_ in changed_evidence:
            print(f"  {cid}:\n    old: {os_}\n    new: {ns_}")


if __name__ == "__main__":
    main()
