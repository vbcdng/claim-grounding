"""Score the two-label grader recheck (task #11).

Compares: fresh Fable-vs-Opus 2-label agreement (overall / problem / control),
the historical baseline on the same rows (their recorded fine-grained labels,
both raw and mechanically collapsed to the same 2-label line), and each fresh
judge vs the WiCE label counted binary.
"""
import json, os

D = os.path.dirname(os.path.abspath(__file__))
rows = {r["row_id"]: r for r in json.load(open(f"{D}/rows.json"))["rows"]}
fab = {v["row_id"]: v["label"] for v in json.load(open(f"{D}/votes_fable.json"))}
opu = {v["row_id"]: v["label"] for v in json.load(open(f"{D}/votes_opus.json"))}
assert set(fab) == set(opu) == set(rows), (len(fab), len(opu), len(rows))

def fable_gold_bin(l):
    return {"supported": "supported", "partial": "not_supported",
            "unsupported": "not_supported"}.get(l)

def opus_hist_bin(l):
    return {"supported": "supported", "add_citation_or_rewrite": "not_supported",
            "wrong_or_insufficient_evidence": "not_supported"}.get(l)

def wice_bin(l):
    return {"supported": "supported", "partially_supported": "not_supported",
            "not_supported": "not_supported"}.get(l)

groups = {"ALL": list(rows),
          "problem": [i for i, r in rows.items() if r["origin"] != "control_agree"],
          "control": [i for i, r in rows.items() if r["origin"] == "control_agree"]}

for name, ids in groups.items():
    n = len(ids)
    fresh = sum(1 for i in ids if fab[i] == opu[i])
    hist_raw = sum(1 for i in ids
                   if rows[i]["fable_gold_label"] == rows[i]["opus_label"] == "supported"
                   or (rows[i]["fable_gold_label"] == "unsupported"
                       and rows[i]["opus_label"] != "supported"))
    hist_bin = sum(1 for i in ids
                   if fable_gold_bin(rows[i]["fable_gold_label"]) == opus_hist_bin(rows[i]["opus_label"]))
    print(f"{name} (n={n}): fresh 2-label Fable=Opus {fresh}/{n} | "
          f"historical labels collapsed to 2: {hist_bin}/{n}")

print("\nFresh disagreements:")
for i in sorted(rows):
    if fab[i] != opu[i]:
        print(f"  {i}: fable={fab[i]} opus={opu[i]} origin={rows[i]['origin']}"
              f" shape={rows[i]['disagreement_shape']}")

wice_ids = [i for i in rows if wice_bin(rows[i]["wice_label"])]
fw = sum(1 for i in wice_ids if fab[i] == wice_bin(rows[i]["wice_label"]))
ow = sum(1 for i in wice_ids if opu[i] == wice_bin(rows[i]["wice_label"]))
print(f"\nvs WiCE label (binary, n={len(wice_ids)}): fable agrees {fw}, opus agrees {ow}")
print("rows where BOTH fresh judges disagree with WiCE-binary:")
for i in wice_ids:
    w = wice_bin(rows[i]["wice_label"])
    if fab[i] != w and opu[i] != w:
        print(f"  {i}: wice={rows[i]['wice_label']} both_fresh={fab[i]}"
              f" fable_gold={rows[i]['fable_gold_label']} origin={rows[i]['origin']}")

fg_ids = [i for i in rows if fable_gold_bin(rows[i]["fable_gold_label"])]
ffg = sum(1 for i in fg_ids if fab[i] == fable_gold_bin(rows[i]["fable_gold_label"]))
ofg = sum(1 for i in fg_ids if opu[i] == fable_gold_bin(rows[i]["fable_gold_label"]))
print(f"\nvs Fable gold (binary, n={len(fg_ids)}): fresh fable {ffg}, fresh opus {ofg}")
