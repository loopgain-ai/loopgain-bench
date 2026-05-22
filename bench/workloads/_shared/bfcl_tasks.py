"""W3 planner-executor corpus: BFCL v4 'multiple' (Berkeley Function Calling Leaderboard).

Per BENCH_PROTOCOL.md Amendment 2026-05-21g (pre-data, scenario-design class),
W3 was escalated from a 15-task inline arithmetic-tools corpus to BFCL v4
after the inline corpus failed the density check at 0% iter-1 failure rate
on Sonnet 4.6. BFCL is the de-facto industry-standard function-calling
benchmark.

The "multiple" subset (199 tasks) is single-turn function calling: the
model sees a user query and a list of candidate functions, picks the
correct one, and provides correct argument values. We chose "multiple"
(not "multi_turn_base") because it grades programmatically without a
simulated environment, matching the bench's per-iteration grading needs.

Grading is a simplified BFCL rule: function name must match the
ground-truth name, and each ground-truth parameter's value must be in the
accepted-value list for that parameter. Error = number of mismatched
parts (1 for wrong function; otherwise count of bad params).

Data files cached at `data/cache/bfcl_v4_multiple.json` and
`bfcl_v4_multiple_answers.json` (one-time pull from
https://raw.githubusercontent.com/ShishirPatil/gorilla/...).
"""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path

CACHE_DIR = Path(__file__).resolve().parents[3] / "data" / "cache"


@lru_cache(maxsize=1)
def _load_bfcl_multiple() -> list[dict]:
    """Load BFCL v4 multiple tasks (questions + functions + answers).

    Returns a list of {id, question, functions, ground_truth} dicts.
    """
    data_path = CACHE_DIR / "bfcl_v4_multiple.json"
    ans_path = CACHE_DIR / "bfcl_v4_multiple_answers.json"
    if not data_path.exists() or not ans_path.exists():
        raise RuntimeError(
            f"BFCL data files missing: expected {data_path} and {ans_path}. "
            f"Fetch via: curl -sSLfo {data_path} "
            f"https://raw.githubusercontent.com/ShishirPatil/gorilla/main/"
            f"berkeley-function-call-leaderboard/bfcl_eval/data/BFCL_v4_multiple.json"
        )

    tasks: dict[str, dict] = {}
    with data_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            tasks[rec["id"]] = {
                "id": rec["id"],
                "question": rec["question"],
                "functions": rec["function"],
            }
    with ans_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            ans = json.loads(line)
            if ans["id"] in tasks:
                tasks[ans["id"]]["ground_truth"] = ans["ground_truth"]

    # Drop tasks missing ground_truth (shouldn't happen but defensive)
    out = [t for t in tasks.values() if "ground_truth" in t]
    out.sort(key=lambda t: int(t["id"].split("_")[-1]))
    return out


# Mock-mode legacy tasks — used when BENCH_MOCK=1 to avoid BFCL data lookup
# from disk during harness smoke tests.
_LEGACY_TASKS = [
    {
        "id": "legacy_0",
        "question": [[{"role": "user", "content": "Compute (3 + 5) * 4 and return the integer."}]],
        "functions": [{"name": "compute", "description": "compute math", "parameters": {"type": "dict", "properties": {"expr": {"type": "string"}}}}],
        "ground_truth": [{"compute": {"expr": ["(3 + 5) * 4", "8 * 4", "32"]}}],
    },
]


class _LazyList:
    def __init__(self, getter):
        self._getter = getter
        self._cache = None

    def _m(self):
        if self._cache is None:
            self._cache = self._getter()
        return self._cache

    def __len__(self):
        return len(self._m())

    def __getitem__(self, i):
        return self._m()[i]

    def __iter__(self):
        return iter(self._m())


def _get_tasks() -> list[dict]:
    if os.environ.get("BENCH_MOCK") == "1" and os.environ.get("BENCH_USE_REAL_CORPUS") != "1":
        return _LEGACY_TASKS
    return _load_bfcl_multiple()


TASKS = _LazyList(_get_tasks)


def get_task(seed: int) -> dict:
    return TASKS[seed % len(TASKS)]


# ---- BFCL-style parsing and grading ----

_CALL_PATTERNS = [
    # JSON: {"name": "...", "args": {...}}  or  {"name": "...", "arguments": {...}}
    re.compile(r'\{[^{}]*"name"\s*:\s*"([^"]+)"[^{}]*"(?:args|arguments|parameters)"\s*:\s*(\{[^{}]*\})[^{}]*\}', re.S),
]


def extract_call(text: str) -> tuple[str, dict] | None:
    """Extract (function_name, args_dict) from the model's text output.

    Accepts:
        - JSON  {"name": "fn", "args": {...}}  / "arguments" / "parameters"
        - Python-like call:  fn_name(arg1=val1, arg2=val2)

    Returns None on failure.
    """
    text = (text or "").strip()
    # Strip code fences
    text = re.sub(r"```(?:json|python|py)?\n?", "", text)
    text = text.replace("```", "").strip()

    # Try parsing as JSON outright
    try:
        rec = json.loads(text)
        if isinstance(rec, dict) and "name" in rec:
            args = rec.get("args") or rec.get("arguments") or rec.get("parameters") or {}
            if isinstance(args, dict):
                return rec["name"], args
        elif isinstance(rec, list) and rec and isinstance(rec[0], dict) and "name" in rec[0]:
            args = rec[0].get("args") or rec[0].get("arguments") or rec[0].get("parameters") or {}
            if isinstance(args, dict):
                return rec[0]["name"], args
    except json.JSONDecodeError:
        pass

    # Try to find a JSON object inside the text
    m = re.search(r"\{[^{}]*\"name\"\s*:\s*\"([^\"]+)\"[^{}]*\}", text, re.S)
    if m:
        try:
            rec = json.loads(m.group(0))
            args = rec.get("args") or rec.get("arguments") or rec.get("parameters") or {}
            if isinstance(args, dict):
                return rec["name"], args
        except json.JSONDecodeError:
            pass

    # Python-like:  fn.name(arg=value, arg2=value)
    m = re.search(r"([A-Za-z_][\w.]*)\s*\(([^)]*)\)", text)
    if m:
        fn_name = m.group(1)
        args_str = m.group(2)
        args: dict = {}
        for part in re.findall(r"([A-Za-z_][\w]*)\s*=\s*([^,]+(?:\([^)]*\)[^,]*)*)", args_str):
            key, val = part
            val = val.strip().strip(",")
            # Coerce literals
            try:
                args[key] = json.loads(val)
            except Exception:
                args[key] = val
        return fn_name, args
    return None


def grade_call(text: str, ground_truth: list[dict]) -> tuple[bool, float, dict]:
    """Grade the model output against BFCL ground_truth.

    BFCL ground_truth shape: a list (usually length 1) of {fn_name: {param: [accepted_values...]}}.
    A param whose accepted-values list includes "" is optional (default acceptable).

    Returns (success, error, debug) — success when error == 0.
    Error contributions:
      - 1 if no call extracted
      - 1 if function name doesn't match any candidate in ground_truth
      - +1 per param where the model's value isn't in the accepted list
      - +1 per missing required param
    """
    call = extract_call(text)
    if call is None:
        return False, 5.0, {"reason": "no_call_extracted", "raw_head": (text or "")[:200]}
    fn_name, args = call

    # Try each ground_truth candidate (BFCL allows multiple correct answers)
    best_err = None
    best_debug = None
    for gt_entry in ground_truth:
        gt_fn = list(gt_entry.keys())[0]
        if fn_name != gt_fn:
            err = 1.0 + len(gt_entry[gt_fn])  # wrong fn = max error for this candidate
            if best_err is None or err < best_err:
                best_err = err
                best_debug = {"reason": "wrong_function", "got": fn_name, "want": gt_fn}
            continue
        gt_params = gt_entry[gt_fn]
        err = 0.0
        param_errors: list[str] = []
        for pname, accepted in gt_params.items():
            if pname not in args:
                # Param missing — OK iff "" is in accepted (optional)
                if "" in accepted:
                    continue
                err += 1
                param_errors.append(f"{pname}: missing")
                continue
            v = args[pname]
            if _value_in_accepted(v, accepted):
                continue
            err += 1
            param_errors.append(f"{pname}: got {v!r}, want one of {accepted!r}")
        if best_err is None or err < best_err:
            best_err = err
            best_debug = {
                "reason": "ok" if err == 0 else "param_mismatch",
                "fn": fn_name,
                "param_errors": param_errors,
            }
    return (best_err == 0), float(best_err), best_debug


def _value_in_accepted(value, accepted: list) -> bool:
    """BFCL-flavored value match: equality OR string-equality OR numeric equality."""
    for cand in accepted:
        if value == cand:
            return True
        if isinstance(value, (int, float)) and isinstance(cand, (int, float)):
            try:
                if abs(float(value) - float(cand)) < 1e-9:
                    return True
            except (ValueError, TypeError):
                pass
        if isinstance(value, str) and isinstance(cand, str):
            if value.strip().lower() == cand.strip().lower():
                return True
        # Lists: order-independent for top-level
        if isinstance(value, list) and isinstance(cand, list):
            if sorted(map(str, value)) == sorted(map(str, cand)):
                return True
    return False
