"""Hardened re-eval worker for the wrong-fixed-point measurement.

Reads JSON {"code","entry_point","cases"} on stdin where cases is a list of
[args_repr, expected_repr] pairs (Python reprs of the call args tuple/list and
the canonical expected value). Execs the model code in process isolation (the
parent SIGKILLs on timeout), calls entry_point(*args) for each case, and reports
TWO comparisons:

  - strict:     got == expected               (order- and type-sensitive)
  - normalized: multiset-equal where both are list/tuple of hashables
                (order-insensitive) else falls back to strict

The gap between them is the order/representation-artifact class (e.g. a
combinations function returning the right elements in a different order). The
wrong-fixed-point metric counts NORMALIZED failures only — genuine wrong values,
not order artifacts.

Writes JSON {"n_total","strict_fail","norm_fail","error"} on stdout. Stdlib only.
"""
from __future__ import annotations

import builtins
import json
import sys
import threading
from collections import Counter

_run_code = builtins.exec
_eval_expr = builtins.eval


def _with_timeout(fn, timeout_s):
    result = [None]; error = [None]
    def _wrap():
        try:
            result[0] = fn()
        except Exception as e:  # noqa: BLE001
            error[0] = e
    t = threading.Thread(target=_wrap, daemon=True)
    t.start(); t.join(timeout=timeout_s)
    if t.is_alive():
        return None, True, None
    return result[0], False, error[0]


def _multiset_eq(a, b):
    """Order-insensitive equality for top-level list/tuple of hashables."""
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        try:
            return Counter(a) == Counter(b)
        except TypeError:
            try:
                return sorted(a) == sorted(b)
            except TypeError:
                return a == b
    return a == b


def main():
    payload = json.loads(sys.stdin.read())
    code = payload["code"]; ep = payload.get("entry_point", "")
    cases = payload.get("cases", [])

    def _do_exec():
        sb = {}
        _run_code(code, sb)
        return sb
    sb, timed_out, exc = _with_timeout(_do_exec, 3.0)
    if timed_out or exc is not None or not sb or ep not in sb:
        # Whole-program failure: every case fails both ways.
        sys.stdout.write(json.dumps({
            "n_total": len(cases), "strict_fail": len(cases),
            "norm_fail": len(cases), "error": "exec_failed"}))
        return
    fn = sb[ep]

    strict_fail = 0; norm_fail = 0
    for args_repr, exp_repr in cases:
        try:
            args = _eval_expr(args_repr, {})
            expected = _eval_expr(exp_repr, {})
        except Exception:
            continue  # unreconstructable case — skip (don't count against the model)

        def _call(_a=args):
            return fn(*_a)
        got, t_o, e = _with_timeout(_call, 1.0)
        if t_o or e is not None:
            strict_fail += 1; norm_fail += 1; continue
        if got != expected:
            strict_fail += 1
            if not _multiset_eq(got, expected):
                norm_fail += 1
    sys.stdout.write(json.dumps({
        "n_total": len(cases), "strict_fail": strict_fail,
        "norm_fail": norm_fail, "error": None}))


if __name__ == "__main__":
    main()
