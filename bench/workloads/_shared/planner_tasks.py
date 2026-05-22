"""Planner-executor tool-use tasks.

Each task is a multi-step problem solvable by chaining a small fixed set of
'tools' (arithmetic / string ops). The model is asked to write a plan, then
the bench simulates tool execution. Error = number of steps required to
reach the goal from the current state, after applying the model's proposed
plan. If the plan is correct, error == 0.

Tools (simulated locally — no real network/agent calls in the iteration's
inner loop):
  - add(a, b), sub(a, b), mul(a, b), div(a, b) — arithmetic
  - upper(s), lower(s), reverse(s), concat(a, b) — strings
  - len(x) — length

The bench passes the *goal* and *initial state* in the prompt. The model
returns a small JSON plan (list of tool calls); the bench executes it and
compares the final result to the expected value.

This is a τ-bench-flavored task (planner-executor with tool calls) without
the τ-bench dataset itself — same shape, narrower scope, fully inline.
"""

from __future__ import annotations

import json
import re

TASKS = [
    {
        "name": "sum_then_mul",
        "goal": "Compute (3 + 5) * 4 and return the integer.",
        "expected": 32,
        "kind": "int",
    },
    {
        "name": "uppercase_word",
        "goal": "Return the string 'hello world' in all uppercase.",
        "expected": "HELLO WORLD",
        "kind": "str",
    },
    {
        "name": "reverse_then_upper",
        "goal": "Reverse the string 'cascade' then uppercase it.",
        "expected": "EDACSAC",
        "kind": "str",
    },
    {
        "name": "concat_lengths",
        "goal": "Concatenate 'loop' and 'gain', then return the length of the result.",
        "expected": 8,
        "kind": "int",
    },
    {
        "name": "compute_avg",
        "goal": "Return the average of 12, 18, and 30 as an integer (use integer division).",
        "expected": 20,
        "kind": "int",
    },
    {
        "name": "string_len_plus_const",
        "goal": "Return the length of 'cascade systems' plus 10.",
        "expected": 25,
        "kind": "int",
    },
    {
        "name": "mul_then_sub",
        "goal": "Compute (7 * 9) - 13 and return the integer.",
        "expected": 50,
        "kind": "int",
    },
    {
        "name": "concat_uppers",
        "goal": "Uppercase 'foo' and 'bar' separately then concatenate them. Return the resulting string.",
        "expected": "FOOBAR",
        "kind": "str",
    },
    {
        "name": "div_then_add",
        "goal": "Compute (100 / 4) + 7 as an integer using integer division.",
        "expected": 32,
        "kind": "int",
    },
    {
        "name": "len_reversed",
        "goal": "Return the length of the reverse of 'agentic ai'.",
        "expected": 10,
        "kind": "int",
    },
    {
        "name": "double_concat",
        "goal": "Concatenate 'ab' and 'cd', then concatenate the result with itself. Return the string.",
        "expected": "abcdabcd",
        "kind": "str",
    },
    {
        "name": "sum_three",
        "goal": "Compute 11 + 22 + 33 and return the integer.",
        "expected": 66,
        "kind": "int",
    },
    {
        "name": "upper_reverse_concat",
        "goal": "Uppercase 'loop', reverse it, then concatenate with 'X'. Return the resulting string.",
        "expected": "POOLX",
        "kind": "str",
    },
    {
        "name": "subtract_lengths",
        "goal": "Return len('barkhausen') - len('loop').",
        "expected": 6,
        "kind": "int",
    },
    {
        "name": "mul_lengths",
        "goal": "Multiply len('cascade') by len('ai') and return the integer.",
        "expected": 14,
        "kind": "int",
    },
]


def get_task(seed: int) -> dict:
    return TASKS[seed % len(TASKS)]


# --- tool executor for the planner-executor pattern ---

def _safe_int(x):
    if isinstance(x, str) and x.lstrip("-").isdigit():
        return int(x)
    return x


def execute_plan(plan: list[dict]) -> tuple[object, list[str]]:
    """Execute a plan (list of tool calls). Return (final_result, errors).

    Each plan step is a dict with keys: `tool`, `args` (list), and optional
    `bind` (str — name to bind result to for reference by `$name` in later
    args). Returns the result of the LAST step.

    A reference like '$step1' in args is resolved to the prior step's bound
    value. Unbound references become an error.
    """
    bindings: dict[str, object] = {}
    last: object = None
    errors: list[str] = []
    for i, step in enumerate(plan):
        tool = step.get("tool")
        args = list(step.get("args", []))
        # Resolve $name references
        for j, a in enumerate(args):
            if isinstance(a, str) and a.startswith("$"):
                key = a[1:]
                if key in bindings:
                    args[j] = bindings[key]
                else:
                    errors.append(f"step{i}: unbound reference {a!r}")
                    args[j] = None
        # Apply common int-coercion for arithmetic tools
        try:
            if tool == "add":
                last = _safe_int(args[0]) + _safe_int(args[1])
            elif tool == "sub":
                last = _safe_int(args[0]) - _safe_int(args[1])
            elif tool == "mul":
                last = _safe_int(args[0]) * _safe_int(args[1])
            elif tool == "div":
                last = _safe_int(args[0]) // _safe_int(args[1])
            elif tool == "upper":
                last = str(args[0]).upper()
            elif tool == "lower":
                last = str(args[0]).lower()
            elif tool == "reverse":
                last = str(args[0])[::-1]
            elif tool == "concat":
                last = str(args[0]) + str(args[1])
            elif tool == "len":
                last = len(args[0])
            else:
                errors.append(f"step{i}: unknown tool {tool!r}")
                continue
        except Exception as exc:
            errors.append(f"step{i}: {tool}({args!r}) -> {exc!r}")
            continue
        bind = step.get("bind")
        if bind:
            bindings[bind] = last
    return last, errors


def extract_plan(text: str) -> list[dict]:
    """Extract the first JSON array of step dicts from text. Returns [] if none."""
    # Try fenced ```json blocks first
    m = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text or "", re.S)
    candidate = m.group(1) if m else None
    if candidate is None:
        # Fall back to first top-level [ ... ]
        m = re.search(r"\[\s*\{.*\}\s*\]", text or "", re.S)
        candidate = m.group(0) if m else None
    if candidate is None:
        return []
    try:
        plan = json.loads(candidate)
        if isinstance(plan, list) and all(isinstance(s, dict) for s in plan):
            return plan
    except json.JSONDecodeError:
        return []
    return []


def score_plan(text: str, task: dict) -> tuple[bool, float, dict]:
    """Try to extract+execute the model's plan, compare to expected.

    Returns (success, error_value, debug_info). error is 0 on success;
    otherwise a positive distance (1 + n_plan_errors for malformed plans;
    2 for a wrong-shape plan; 3 for no plan at all).
    """
    plan = extract_plan(text)
    if not plan:
        return False, 3.0, {"reason": "no_plan", "raw": (text or "")[:200]}
    result, errors = execute_plan(plan)
    if errors:
        return False, 1.0 + len(errors), {"reason": "exec_errors", "errors": errors, "result": repr(result)}
    expected = task["expected"]
    if task["kind"] == "int":
        try:
            ok = int(result) == int(expected)
        except (ValueError, TypeError):
            ok = False
    else:
        ok = str(result) == str(expected)
    if ok:
        return True, 0.0, {"result": repr(result)}
    return False, 2.0, {"reason": "wrong_value", "got": repr(result), "want": repr(expected)}
