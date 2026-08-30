"""Split each arm's mistakes into the two kinds that matter to an author:
wrongly objecting to a sound citation, and letting a faulty one through."""
import json, os, sys
sys.path.insert(0, os.path.abspath("benchmarks"))
from citation_integrity_bench import evaluate, grounding_side

D = "data/citation_integrity/batch_dev_pilot100"
gt = json.load(open(f"{D}/ci_ground_truth.json", encoding="utf-8"))
DEFAULT_LEGS = [("free Google", f"{D}_run_task31_gglB"),
                ("paid full precision", f"{D}_run_task31_bf16only"),
                ("paid four-bit (run 1)", f"{D}_run_task31_openrouter"),
                ("paid four-bit (run 2)", f"{D}_run_task31_openrouter2")]

# Extra arms can be named on the command line as "label=run directory", e.g.
#   python3 benchmarks/task31_error_kinds.py "free Gemma 4 26B=data/.../run_gemma4_26b"
# Named arms are ADDED to the four above so a new model is always read against
# them; pass --only to replace them instead.
args = [a for a in sys.argv[1:] if a != "--only"]
legs = ([] if "--only" in sys.argv[1:] else list(DEFAULT_LEGS)) + [
    (a.split("=", 1)[0], a.split("=", 1)[1]) for a in args if "=" in a]

sides = [grounding_side(v.get("label")) for v in gt["claims"].values()]
print("answer key balance of the 100 rows: %d sound citations (should pass), "
      "%d faulty ones (should be flagged), %d not scored"
      % (sides.count("pass"), sides.count("flag"),
         sum(1 for s in sides if s not in ("pass", "flag"))))
print()
print("%-24s %6s %18s %16s %14s" % ("arm", "score", "wrong objections", "let through", "escalations"))
for name, rundir in legs:
    a = json.load(open(f"{rundir}/analysis.json", encoding="utf-8"))
    rows = evaluate(a, gt)["rows"]
    ok = fa = miss = 0
    esc = sum(1 for r in rows if r.get("method") in ("llm_fulltext",
              "component_rescue", "tail_rescue", "arbiter_rescue"))
    for r in rows:
        key = grounding_side(r["label"])
        tool = r.get("own")
        if key not in ("pass", "flag") or tool not in ("pass", "flag"):
            continue
        if tool == key:
            ok += 1
        elif key == "pass":
            fa += 1          # citation was sound, tool objected
        else:
            miss += 1        # citation was faulty, tool accepted it
    print("%-24s %6d %18d %16d %14d" % (name, ok, fa, miss, esc))
