"""Card S1 (card 395): a chain is a small rule file that turns saved answers into decisions.

Rule file (JSON):
{"name": "gemma_then_sonnet",
 "answers": {"g": "answers/gemma_paid.jsonl", "s": "answers/sonnet.jsonl"},   # relative to the rule file
 "params": {"t": 0.5},                                                       # optional
 "fit": {"param": "t", "grid": [0.3, 0.5, 0.7], "minimize": "wrongly_passed", "max_undecided": 0.2},
 "rules": [{"name": "cheap", "if": [{"step": "g", "is": "supported"}, {"step": "g", "quote_passes": true}],
            "then": "supported"},
           {"name": "sonnet", "if": [{"step": "s", "is": ["supported", "not_supported"]}], "then": "$s"}],
 "otherwise": "undecided"}

The first rule whose conditions all hold settles the piece; "then" is an answer or "$<step>" (that
step's answer). Conditions on one step (the step's answer is its FIRST repeat; later repeats are
only a signal of agreement):
  {"step": X, "is": answer or [answers]}           {"step": X, "reason_in": [reason kinds]}
  {"step": X, "has_detail": true}                  {"step": X, "repeats_agree": true}
  {"step": X, "quote_in_work": true}               {"step": X, "quote_passes": true}
  {"step": X, "score_at_least": number or "$p"}    {"step": X, "score_below": number or "$p"}
and {"not": c}, {"any": [c, ...]}, {"all": [c, ...]}. A step with no usable answer for the piece
(missing, or the call failed) makes every condition on it false.
Before the rules, step 0: the writer's own -> "skipped", a missing source -> "not_checked". When no
rule fires and any step of the file failed or is missing for the piece, the answer is
"not_checked" (a failed call is never a verdict, and the chain might have settled the piece had
the call worked); otherwise "otherwise" (default "undecided").

Cost: a decision carries the dollars and input tokens of the steps the chain consulted, in rule
order, up to the rule that settled it (all repeats of a step whose repeats were used).

--folds: the five-fold check on the tuning part. For each fold the "fit" parameter is chosen from
its grid on the other four folds (fewest of "minimize", with at most max_undecided undecided; ties:
fewer undecided, then grid order), and the fold's pieces are decided with it. Answers and labels
are read for the tuning part only, unless --final (then no folds).
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from sprint_shared_load import sibling  # noqa: E402

ask = sibling("ask")
quote_check = sibling("quote_check")
records = sibling("records")
score = sibling("score")
split = sibling("split")

FINAL_ANSWERS = ("supported", "not_supported", "undecided", "not_checked", "skipped", "unsure")


class Chain:
    def __init__(self, rule, base_dir, final=False, split_path=split.SPLIT):
        self.rule = rule
        self.final = final
        self.answers = {}
        for step, rel in rule["answers"].items():
            path = Path(rel) if Path(rel).is_absolute() else Path(base_dir) / rel
            self.answers[step] = ask.load_answers(path, final=final, split_path=split_path)
        self.pieces = {r["piece_id"]: r for r in records.load_pieces()}
        self._works = {}
        for r in rule["rules"]:
            if r.get("then", "").startswith("$") and r["then"][1:] not in self.answers:
                raise SystemExit(f"refused: rule {r.get('name')} answers with unknown step {r['then']}")

    def first(self, step, pid):
        reps = self.answers[step].get(pid) or []
        r0 = next((r for r in reps if r["repeat"] == 0), None)
        if r0 is None or r0.get("failed") or r0.get("answer") is None:
            return None
        return r0

    def works(self, pid):
        if pid not in self._works:
            self._works[pid] = [records.work_text(w["file"]) for w in self.pieces[pid]["cited_works"]]
        return self._works[pid]

    def holds(self, c, pid, params, used):
        if "not" in c:
            return not self.holds(c["not"], pid, params, used)
        if "any" in c:
            return any(self.holds(x, pid, params, used) for x in c["any"])
        if "all" in c:
            return all(self.holds(x, pid, params, used) for x in c["all"])
        step = c["step"]
        used.setdefault(step, False)
        r = self.first(step, pid)
        if r is None:
            return False
        val = lambda v: params[v[1:]] if isinstance(v, str) and v.startswith("$") else v  # noqa: E731
        if "is" in c:
            want = c["is"] if isinstance(c["is"], list) else [c["is"]]
            return r["answer"] in want
        if "reason_in" in c:
            return r.get("reason_kind") in c["reason_in"]
        if "has_detail" in c:
            return bool((r.get("detail") or "").strip()) == bool(c["has_detail"])
        if "repeats_agree" in c:
            used[step] = True
            reps = [x for x in self.answers[step].get(pid) or [] if not x.get("failed")]
            agree = len(reps) > 1 and len({x["answer"] for x in reps}) == 1
            return agree == bool(c["repeats_agree"])
        if "quote_in_work" in c:
            return quote_check.quote_in_work(r.get("quote"), self.works(pid)) == bool(c["quote_in_work"])
        if "quote_passes" in c:
            res = quote_check.check(self.pieces[pid]["piece"], r.get("quote") or "", self.works(pid))
            return res["passed"] == bool(c["quote_passes"])
        if "score_at_least" in c:
            return r.get("jev_score") is not None and r["jev_score"] >= float(val(c["score_at_least"]))
        if "score_below" in c:
            return r.get("jev_score") is not None and r["jev_score"] < float(val(c["score_below"]))
        raise SystemExit(f"refused: unknown condition {c}")

    def cost(self, pid, used):
        usd, toks = {}, {}
        for step, all_reps in used.items():
            reps = self.answers[step].get(pid) or []
            if not all_reps:
                reps = [r for r in reps if r["repeat"] == 0]
            usd[step] = round(sum(float(r.get("cost_usd") or 0.0) for r in reps), 8)
            toks[step] = sum(int(r.get("input_tokens") or 0) for r in reps)
        return usd, toks

    def decide(self, pid, params=None):
        params = dict(self.rule.get("params") or {}, **(params or {}))
        s0 = records.step0(self.pieces[pid])
        if s0:
            return {"piece_id": pid, "answer": s0, "step": "step0", "cost_usd": 0.0}
        used = {}
        for r in self.rule["rules"]:
            if all(self.holds(c, pid, params, used) for c in r["if"]):
                ans = r["then"]
                if ans.startswith("$"):
                    ans = self.first(ans[1:], pid)["answer"]
                    used.setdefault(r["then"][1:], False)
                if ans == "unsure":
                    ans = "undecided"
                usd, toks = self.cost(pid, used)
                return {"piece_id": pid, "answer": ans, "step": r.get("name", "rule"),
                        "cost_usd": round(sum(usd.values()), 8), "cost_by_step": usd, "tokens": toks}
        for step in self.answers:
            used.setdefault(step, False)
        usd, toks = self.cost(pid, used)
        none = any(self.first(s, pid) is None for s in self.answers)
        return {"piece_id": pid, "answer": "not_checked" if none else self.rule.get("otherwise", "undecided"),
                "step": "failed calls" if none else "otherwise", "cost_usd": round(sum(usd.values()), 8),
                "cost_by_step": usd, "tokens": toks}

    def piece_ids(self, split_path=split.SPLIT):
        return sorted(split.allowed_ids(self.final, split_path) & set(self.pieces))


def objective(decisions, labels, ids, what):
    r = score.rates(ids, labels, decisions)
    k = {"wrongly_passed": r["wrongly_passed"][0], "wrongly_rejected": r["wrongly_rejected"][0],
         "mistakes": r["wrongly_passed"][0] + r["wrongly_rejected"][0]}[what]
    return k, r["undecided"][0], r["n"]


def five_fold(chain, split_path=split.SPLIT):
    """Decisions for every tuning piece, each with the parameter fitted on the other four folds."""
    fit = chain.rule.get("fit")
    if not fit:
        raise SystemExit("refused: --folds needs a 'fit' section in the rule file")
    labels = records.load_labels(final=False, split_path=split_path)
    fold = split.fold_of_claim(split_path)
    ids = [p for p in chain.piece_ids(split_path) if p in labels]
    by_fold = {}
    for p in ids:
        by_fold.setdefault(fold[labels[p]["row_id"]], []).append(p)
    table = {v: {p: chain.decide(p, {fit["param"]: v}) for p in ids} for v in fit["grid"]}
    out, chosen = {}, []
    for k in sorted(by_fold):
        train = [p for p in ids if p not in set(by_fold[k])]
        best = None
        for gi, v in enumerate(fit["grid"]):
            bad, und, n = objective(table[v], labels, train, fit.get("minimize", "mistakes"))
            if n and und / n > fit.get("max_undecided", 1.0):
                continue
            key = (bad, und, gi)
            if best is None or key < best[0]:
                best = (key, v)
        v = best[1] if best else fit["grid"][0]
        chosen.append((k, v))
        for p in by_fold[k]:
            out[p] = dict(table[v][p], params={fit["param"]: v}, fold=k)
    return out, chosen


def main(argv=None):
    ap = argparse.ArgumentParser(description="Card S1: turn saved answers into decisions with a rule file. "
                                             "Tuning part only, unless --final.")
    ap.add_argument("rule", help="rule file (JSON)")
    ap.add_argument("--out", required=True, help="decisions file (jsonl) for score.py")
    ap.add_argument("--folds", action="store_true", help="five-fold check of the fitted parameter")
    ap.add_argument("--final", action="store_true", help="decide the held-back part too (card F only)")
    ap.add_argument("--pieces", help="only these piece ids (one per line, or a JSON list)")
    a = ap.parse_args(argv)
    if a.final and a.folds:
        raise SystemExit("refused: the five-fold check runs on the tuning part only")
    if a.final:
        score.log_final(["combine.py"] + list(argv if argv is not None else sys.argv[1:]))
    rule_path = Path(a.rule)
    rule = json.loads(rule_path.read_text(encoding="utf-8"))
    chain = Chain(rule, rule_path.parent, final=a.final)
    if a.folds:
        dec, chosen = five_fold(chain)
        print("fitted per fold:", ", ".join(f"fold {k}: {v}" for k, v in chosen))
    else:
        dec = {p: chain.decide(p) for p in chain.piece_ids()}
    if a.pieces:
        keep = score.read_ids(a.pieces)
        dec = {p: d for p, d in dec.items() if p in keep}
    with open(a.out, "w", encoding="utf-8") as f:
        for p in sorted(dec):
            f.write(json.dumps(dict(dec[p], rule=rule.get("name")), ensure_ascii=False) + "\n")
    counts = {}
    for d in dec.values():
        counts[d["answer"]] = counts.get(d["answer"], 0) + 1
    print(f"{rule.get('name')}: {len(dec)} decisions {dict(sorted(counts.items()))}; {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
