"""Card S1 (card 395): ask one step one question file for a list of pieces; save one line per answer.

Steps: stand_in (no model; for tests and the worked example), gemma_free, gemma_paid, jev,
haiku, sonnet, opus. One line per piece and repeat in the answers file (jsonl):
piece_id, step, model, served_model, field_set, question, repeat, answer (supported /
not_supported / unsure, or skipped / not_checked from step 0, or null when the call failed),
failed, quote, reason_kind (unproven / differs_in_detail / contradicted), detail, sureness (only
when the model gives one), jev_score, cost_usd, input_tokens, output_tokens, prompt_sha1, given
(per cited work "whole" or "passages"), error, reply, ts.

Rules (card 394's lessons):
- A failed, refused, empty or unreadable call is saved with failed=true and answer null; it counts
  as "not checked" (combine.py), never as a verdict. A restart asks it again. Five pieces in a row
  with every call failed stop the run (exit 5), so a dead key is not filed piece by piece.
- A restart skips every piece and repeat already answered with the same prompt (prompt_sha1).
- Repeat 0 is asked at temperature 0 and is the step's answer; repeats 1.. (temperature 0.7) only
  show whether the model is stable on the piece (Gemma: --repeats 3).
- Money: gemma_paid needs AUTHOR_GO and a dollar ceiling (--max-usd or MAX_USD), jev needs
  JEV_AUTHOR_GO and the ceiling (ladder_ask.require_paid_go); before every paid call the worst
  case of that call is added to what the file already spent and the run stops (exit 4) before it
  could pass the ceiling. Jev's weekly limit is checked by jev_client itself. Paid steps run only
  through the paid line of the queue (standing ruling 1). gemma_free runs under FREE_GOOGLE_ONLY=1.
- Claude steps go through the subscription only (claude-code/<tier>, never an API key). The
  share guard of card 374 is inside the claude-code backend; the wait-at-the-limit guard of
  card 160 (arm_guard.install_claude_guard) is installed before the first call.

load_answers(path, final=False) gives only the tuning part unless final=True; with
label_free=True it gives every piece, for counts that read no label (compare_fields.py).
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
for p in (str(HERE), str(REPO), str(REPO / "benchmarks"), str(REPO / "benchmarks" / "conversion_key")):
    if p not in sys.path:
        sys.path.insert(0, p)

from sprint_shared_load import sibling  # noqa: E402

records = sibling("records")
split = sibling("split")

STEPS = {"stand_in": "stand-in", "gemma_free": "gemini/gemma-4-31b-it",
         "gemma_paid": "openrouter/google/gemma-4-31b-it", "jev": "jev-latest",
         "haiku": "claude-code/haiku", "sonnet": "claude-code/sonnet", "opus": "claude-code/opus"}
PAID = ("gemma_paid", "jev")
CLAUDE = ("haiku", "sonnet", "opus")
DEFAULT_QUESTION = HERE / "questions" / "neutral_v1.md"
MAX_OUTPUT_TOKENS = 600
MAX_FAILED_IN_A_ROW = 5
ANSWERS = {"supported": "supported", "not supported": "not_supported", "not_supported": "not_supported",
           "unsupported": "not_supported", "unsure": "unsure"}
REASONS = {"not in the work": "unproven", "not_in_the_work": "unproven", "unproven": "unproven",
           "differs in a detail": "differs_in_detail", "differs_in_detail": "differs_in_detail",
           "contradicted": "contradicted", "the work says the opposite": "contradicted"}
JEV_QUESTION = ("Does the cited work support the statement? True only when the cited work itself says "
                "what the statement says, at least as strongly as the writer did.")


class MoneyLimitReached(RuntimeError):
    pass


class CallsKeepFailing(RuntimeError):
    pass


def now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def sha1(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ replies
def parse_reply(text):
    """The neutral question's JSON reply -> dict, or None when it cannot be read."""
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except ValueError:
        return None
    if not isinstance(d, dict):
        return None
    ans = ANSWERS.get(str(d.get("answer", "")).strip().lower())
    if ans is None:
        return None
    reason = REASONS.get(str(d.get("reason") or "").strip().lower()) if ans == "not_supported" else None
    sure = d.get("sureness", d.get("confidence"))
    return {"answer": ans, "quote": str(d.get("quote") or ""), "reason_kind": reason,
            "detail": str(d.get("detail") or ""),
            "sureness": float(sure) if isinstance(sure, (int, float)) else None}


# ------------------------------------------------------------------ callers
def stand_in_caller(fail_every=0):
    """No model. Answers from the prompt's sha1: even -> supported with the work's first sentence as
    the quote, odd -> not supported, differs in a detail. With fail_every=n every n-th call fails."""
    state = {"n": 0}

    def call(prompt, temperature):
        state["n"] += 1
        if fail_every and state["n"] % fail_every == 0:
            return None, {"error": "stand-in failure (fail_every)"}
        body = prompt.split(":\n", 1)[1] if "CITED WORK" not in prompt else prompt.split("CITED WORK", 1)[1]
        body = body.split("\n", 1)[1] if "\n" in body else body
        quote = re.split(r"(?<=[.!?])\s", body.strip(), maxsplit=1)[0][:200]
        h = int(sha1(prompt + f"|{temperature}"), 16)
        if h % 2 == 0:
            d = {"answer": "supported", "quote": quote, "reason": None, "detail": ""}
        else:
            d = {"answer": "not supported", "quote": quote, "reason": "differs in a detail",
                 "detail": "stand-in detail"}
        return json.dumps(d), {"prompt_tokens": len(prompt) // 4, "completion_tokens": 40}
    return call


def litellm_caller(model, purpose):
    from ladder_ask import llm_caller
    inner = llm_caller(model, purpose)

    def call(prompt, temperature):
        try:
            return inner(prompt, temperature)
        except Exception as e:  # noqa: BLE001 — every failure is saved as failed
            return None, {"error": f"{type(e).__name__}: {e}"[:300]}
    return call


def claude_caller(step, out_dir):
    from modules.papertrail import claude_code_backend
    from card160 import arm_guard
    from card183_haiku_arbiter import UsageClaudeCodeClient
    arm_guard.install_claude_guard(claude_code_backend, str(out_dir), arm=f"sprint_{step}", out_dir=str(out_dir))
    usage = Path(out_dir) / f"{step}_usage.jsonl"
    client = UsageClaudeCodeClient(STEPS[step], str(usage))

    def call(prompt, temperature):
        try:
            text = client.call(prompt, temperature=temperature, max_output_tokens=MAX_OUTPUT_TOKENS,
                               purpose="sprint_ask")
        except Exception as e:  # noqa: BLE001
            return None, {"error": f"{type(e).__name__}: {e}"[:300]}
        info = {}
        try:
            last = json.loads(usage.read_text(encoding="utf-8").splitlines()[-1])
            u = last.get("usage") or {}
            info = {"prompt_tokens": int(u.get("input_tokens") or 0)
                    + int(u.get("cache_read_input_tokens") or 0) + int(u.get("cache_creation_input_tokens") or 0),
                    "completion_tokens": int(u.get("output_tokens") or 0),
                    "served_model": ",".join(sorted((last.get("modelUsage") or {}).keys())) or None}
        except (OSError, ValueError, IndexError):
            pass
        return (text or None), info
    return call


def jev_state(inp):
    f = inp["fields"]
    lead = {"possible": "The writer says it is possible that: ",
            "likely": "The writer says it is likely that: "}.get(f.get("certainty"), "")
    state = {"statement": lead + f["piece"]}
    if f.get("earlier_sentence"):
        state["sentence_before_it"] = f["earlier_sentence"]
    for i, w in enumerate(inp["works"], 1):
        state[f"cited_work_{i}"] = w["text"]
    return state


# ------------------------------------------------------------------ the store
def read_lines(path):
    if not Path(path).exists():
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def append(path, rec):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def done_keys(path):
    """(piece_id, repeat) -> prompt_sha1 of answers that need no new call (failed ones are asked again)."""
    return {(r["piece_id"], r["repeat"]): r["prompt_sha1"] for r in read_lines(path) if not r.get("failed")}


def spent(path):
    return sum(float(r.get("cost_usd") or 0.0) for r in read_lines(path))


def load_answers(path, final=False, label_free=False, split_path=split.SPLIT):
    """piece_id -> [answer of repeat 0, repeat 1, ...] (the last line saved for each repeat).
    Tuning part only, unless final=True; label_free=True gives every piece (no label is read)."""
    ok = None if (final or label_free) else split.allowed_ids(False, split_path)
    latest = {}
    for r in read_lines(path):
        if ok is None or r["piece_id"] in ok:
            latest[(r["piece_id"], r["repeat"])] = r
    out = {}
    for (pid, rep), r in sorted(latest.items()):
        out.setdefault(pid, []).append(r)
    return out


# ------------------------------------------------------------------ asking
def base(inp, step, model, question, rep):
    return {"piece_id": inp["piece_id"], "step": step, "model": model, "field_set": inp["field_set"],
            "question": question, "repeat": rep}


def ask_pieces(recs, step, question_path, set_name, out_path, repeats=1, caller=None, client=None,
               price=None, limit_usd=None):
    """Ask every piece of `recs`; returns the number of new lines saved."""
    template = records.load_question(question_path)
    qname = Path(question_path).name
    model = STEPS[step]
    done = done_keys(out_path)
    saved = 0
    failed_in_a_row = 0
    wrong = None
    if records.load_field_set(set_name).get("wrong_values"):
        wrong_fields = sibling("wrong_fields")
        wrong = wrong_fields.wrong_values()
    for rec in recs:
        inp = records.step_input(rec, set_name, step, wrong=wrong)
        if inp["step0"]:
            key = (rec["piece_id"], 0)
            if done.get(key) != "step0":
                append(out_path, dict(base(inp, step, None, qname, 0), answer=inp["step0"], failed=False,
                                      cost_usd=0.0, prompt_sha1="step0", given={}, ts=now()))
                saved += 1
            continue
        given = {w["key"]: w["given"] for w in inp["works"]}
        if step == "jev":
            state = jev_state(inp)
            prompt = json.dumps({"q": JEV_QUESTION, "state": state}, ensure_ascii=False, sort_keys=True)
        else:
            prompt = records.render(template, inp)
        psha = sha1(prompt)
        all_failed = True
        for rep in range(repeats if step != "jev" else 1):
            if done.get((rec["piece_id"], rep)) == psha:
                all_failed = False
                continue
            line = dict(base(inp, step, model, qname if step != "jev" else "jev_supports_v1", rep),
                        prompt_sha1=psha, given=given)
            if step == "jev":
                line.update(_ask_jev(client, state, rec["piece_id"], out_path, limit_usd))
            else:
                line.update(_ask_text(caller, prompt, rep, out_path, price, limit_usd))
            line["ts"] = now()
            append(out_path, line)
            saved += 1
            all_failed = all_failed and line["failed"]
        failed_in_a_row = failed_in_a_row + 1 if all_failed else 0
        if failed_in_a_row >= MAX_FAILED_IN_A_ROW:
            raise CallsKeepFailing(f"{failed_in_a_row} pieces in a row got no answer at all; the run stops")
    return saved


def _usd(tin, tout, price):
    return tin / 1e6 * price[0] + tout / 1e6 * price[1]


def _ask_text(caller, prompt, rep, out_path, price, limit_usd):
    if limit_usd is not None and price is not None:
        worst = 3 * _usd(len(prompt.encode("utf-8")), 2 * MAX_OUTPUT_TOKENS, price)
        if spent(out_path) + worst > limit_usd:
            raise MoneyLimitReached(f"the next call could pass the ceiling of ${limit_usd:.2f}")
    temp = 0.0 if rep == 0 else 0.7
    text, info = caller(prompt, temp)
    parsed = parse_reply(text)
    if text is not None and parsed is None:
        text2, info2 = caller(prompt, 0.4)       # one more try for an unreadable reply
        info = {k: (info.get(k, 0) or 0) + (info2.get(k, 0) or 0) if k.endswith("tokens") else info2.get(k, info.get(k))
                for k in set(info) | set(info2)}
        text, parsed = text2, parse_reply(text2)
    tin, tout = int(info.get("prompt_tokens") or 0), int(info.get("completion_tokens") or 0)
    if price is not None and not tin:
        tin, tout = len(prompt) // 3 + 1, max(tout, 60)     # a paid call is never saved as free
    out = {"input_tokens": tin, "output_tokens": tout,
           "cost_usd": round(_usd(tin, tout, price), 8) if price else round(float(info.get("client_cost_usd") or 0.0), 8),
           "served_model": info.get("served_model"), "reply": (text or "")[:600]}
    if parsed is None:
        out.update(answer=None, failed=True,
                   error=info.get("error") or ("no reply" if not text else "reply not readable"))
    else:
        out.update(parsed, failed=False)
    return out


def _ask_jev(client, state, case, out_path, limit_usd):
    from modules.papertrail import jev_client
    q = jev_client.Question(name="supports", kind="noul", instructions=JEV_QUESTION,
                            criteria={"true": "the cited work says what the statement says",
                                      "false": "the cited work does not say it, says something different, "
                                               "or says the opposite"})
    if limit_usd is not None:
        toks = len(json.dumps(state)) // 3 + 400
        if spent(out_path) + 3 * jev_client.cost_usd(toks) > limit_usd:
            raise MoneyLimitReached(f"the next call could pass the ceiling of ${limit_usd:.2f}")
    a = client.ask(state, [q], case=case)
    score = (a.answers.get("supports") or {}).get("noul") if not a.failed else None
    ok = isinstance(score, (int, float))
    return {"answer": ("supported" if score >= 0.5 else "not_supported") if ok else None,
            "jev_score": float(score) if ok else None, "failed": not ok, "error": a.error,
            "cost_usd": round(float(a.cost_usd), 8), "input_tokens": int(a.input_tokens), "output_tokens": 0,
            "served_model": getattr(a, "served_model", None)}


# ------------------------------------------------------------------ command line
def piece_list(a):
    recs = records.load_pieces()
    if a.all:
        return recs
    if a.field_test:
        wrong_fields = sibling("wrong_fields")
        ids = wrong_fields.load_ids()["pieces"]
    else:
        text = Path(a.pieces).read_text(encoding="utf-8").strip()
        ids = json.loads(text) if text.startswith("[") else [x.strip() for x in text.splitlines() if x.strip()]
    want = set(ids)
    unknown = want - {r["piece_id"] for r in recs}
    if unknown:
        raise SystemExit(f"refused: unknown piece ids {sorted(unknown)[:5]}")
    return [r for r in recs if r["piece_id"] in want]


def parser():
    ap = argparse.ArgumentParser(description="Card S1: ask one step one question file for a list of pieces "
                                             "and save one line per answer. Paid steps only through the paid "
                                             "line of the queue.")
    ap.add_argument("--step", required=True, choices=sorted(STEPS))
    ap.add_argument("--question", default=str(DEFAULT_QUESTION))
    ap.add_argument("--field-set", default="fields_v1")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--all", action="store_true", help="all 300 pieces")
    g.add_argument("--pieces", help="file of piece ids (one per line, or a JSON list)")
    g.add_argument("--field-test", action="store_true", help="the 65 pieces of field_test_ids.json")
    ap.add_argument("--out", required=True, help="answers file (jsonl); a restart continues it")
    ap.add_argument("--repeats", type=int, default=1, help="Gemma: 3 (the first answer counts)")
    ap.add_argument("--max-usd", type=float, help="dollar ceiling of a paid step (or MAX_USD)")
    ap.add_argument("--seller", help="gemma_paid: pin one seller of ladder_ask.GEMMA_SELLERS")
    ap.add_argument("--estimate", action="store_true", help="print calls and size; send nothing")
    ap.add_argument("--stand-in-fail-every", type=int, default=0, help="stand_in: every n-th call fails")
    return ap


def main(argv=None, env=None):
    a = parser().parse_args(argv)
    env = dict(os.environ) if env is None else env
    recs = piece_list(a)
    out = Path(a.out)
    records.load_field_set(a.field_set)
    if a.estimate:
        chars = n = 0
        for r in recs:
            inp = records.step_input(r, a.field_set, a.step)
            if not inp["step0"]:
                n += 1
                chars += sum(len(w["text"]) for w in inp["works"]) + 2500
        reps = 1 if a.step == "jev" else a.repeats
        line = f"{a.step}: {n} pieces x {reps} calls, about {chars * reps // 3:,} input tokens"
        if a.step == "gemma_paid":
            from ladder_ask import prices_for
            p = prices_for("gemma", STEPS[a.step], a.seller)
            line += f", at most about ${_usd(chars * reps // 3, 300 * n * reps, p):.2f} (sent ${p[0]}/M)"
        print(line + " (estimate; nothing sent)")
        return 0
    price = limit = caller = client = None
    if a.step in PAID:
        from ladder_ask import require_paid_go, prices_for
        if a.step == "jev":
            from modules.papertrail import jev_client
            limit = require_paid_go(env, a.max_usd, go_var=jev_client.GO_ENV_VAR)
            client = jev_client.JevClient(log_path=str(out.parent / "jev_calls.jsonl"), dry_run=False)
        else:
            limit = require_paid_go(env, a.max_usd)
            price = prices_for("gemma", STEPS[a.step], a.seller)
            if a.seller:
                os.environ["PAPERTRAIL_LLM_EXTRA_BODY"] = json.dumps(
                    {STEPS[a.step]: {"provider": {"order": [a.seller], "allow_fallbacks": False}}})
            os.environ.pop("FREE_GOOGLE_ONLY", None)
            caller = litellm_caller(STEPS[a.step], "sprint_ask")
    elif a.step == "gemma_free":
        os.environ["FREE_GOOGLE_ONLY"] = "1"
        caller = litellm_caller(STEPS[a.step], "sprint_ask")
    elif a.step in CLAUDE:
        caller = claude_caller(a.step, out.parent)
    else:
        caller = stand_in_caller(a.stand_in_fail_every)
    try:
        n = ask_pieces(recs, a.step, a.question, a.field_set, out, a.repeats, caller, client, price, limit)
    except MoneyLimitReached as e:
        print(f"stopped: {e}")
        return 4
    except CallsKeepFailing as e:
        print(f"stopped: {e}")
        return 5
    lines = read_lines(out)
    failed = sum(1 for r in lines if r.get("failed"))
    print(f"{a.step} / {a.field_set}: {n} new lines; file holds {len(lines)} lines, {failed} failed "
          f"(asked again on a restart), ${spent(out):.4f} spent; {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
