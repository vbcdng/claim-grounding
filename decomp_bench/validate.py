#!/usr/bin/env python3
"""PASS/FAIL checker for canonical decomp_bench JSONL files.

This is the contract any converter (including LLM-written ones for external
tools) must satisfy: `validate.py <file.jsonl>` exits 0 on PASS, 1 on FAIL
with the first errors listed. Pure stdlib, no LLM.
"""
import json
import sys

REQUIRED = {
    "claim_id": str, "doc_id": str, "corpus": str, "tool": str,
    "tool_version": str, "claim": str, "evidence": list,
    "evidence_pages": list, "order": int, "auto_flags": list,
    "llm_reason": str, "note": str,
}
OK_VALUES = (None, "y", "n")
MAX_ERRORS = 20


def check(path: str) -> list:
    errors = []
    seen_ids = set()
    n = 0
    for lineno, line in enumerate(open(path, encoding="utf-8"), 1):
        line = line.strip()
        if not line:
            continue
        n += 1
        try:
            r = json.loads(line)
        except json.JSONDecodeError as e:
            errors.append(f"line {lineno}: not valid JSON ({e})")
            continue
        for key, typ in REQUIRED.items():
            if key not in r:
                errors.append(f"line {lineno}: missing key '{key}'")
            elif not isinstance(r[key], typ):
                errors.append(f"line {lineno}: '{key}' must be {typ.__name__}")
        for key in ("human_ok", "llm_ok"):
            if r.get(key) not in OK_VALUES:
                errors.append(f"line {lineno}: '{key}' must be null/'y'/'n'")
        if isinstance(r.get("claim"), str) and not r["claim"].strip():
            errors.append(f"line {lineno}: empty claim text")
        if isinstance(r.get("evidence"), list) and any(
                not isinstance(e, str) for e in r["evidence"]):
            errors.append(f"line {lineno}: evidence must be list of str")
        cid = r.get("claim_id")
        if cid in seen_ids:
            errors.append(f"line {lineno}: duplicate claim_id '{cid}'")
        seen_ids.add(cid)
        if len(errors) >= MAX_ERRORS:
            errors.append("... (stopping at 20 errors)")
            break
    if n == 0:
        errors.append("file has no records")
    return errors


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: validate.py <file.jsonl>")
    errors = check(sys.argv[1])
    if errors:
        print("FAIL")
        print("\n".join(errors))
        sys.exit(1)
    print("PASS")


if __name__ == "__main__":
    main()
