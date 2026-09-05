"""Candidate evaluator, launched only through the Docker sandbox backend.

The container is the security boundary. Inner thread deadlines give assertion
feedback; the parent enforces output/resource limits and removes the container
on every exit, including a GIL-holding candidate or surviving descendants.

Protocol: stdin JSON {"code","entry_point","tests"}; stdout JSON
{"n_passing","n_total","failed"}. Stdlib only.
"""
from __future__ import annotations

import builtins
import json
import sys
import threading
import time

# Interpreter primitives intentionally execute candidates inside the container.
_run_code = builtins.exec
_eval_expr = builtins.eval


def _with_timeout(fn, timeout_s: float):
    """Run fn() in a daemon thread; return (result, timed_out, exception).

    Identical to the historical in-process guard. On timeout the daemon thread
    is leaked; in this isolated worker that is harmless — the parent SIGKILLs
    the whole process if it fails to exit in time.
    """
    result: list = [None]
    error: list = [None]

    def _wrap():
        try:
            result[0] = fn()
        except Exception as e:  # noqa: BLE001
            error[0] = e

    t = threading.Thread(target=_wrap, daemon=True)
    t.start()
    t.join(timeout=timeout_s)
    if t.is_alive():
        return None, True, None
    return result[0], False, error[0]


def run_tests(code: str, entry_point: str, tests: list[str]) -> tuple[int, int, list[str]]:
    """Exec the candidate code in a fresh dict, then evaluate each assertion.

    Budget: 3s for exec, 1s per assertion, 7s overall. Compile/runtime errors
    on the code count as ALL tests failing. Mirrors the prior in-process logic.
    """

    def _do_exec() -> dict:
        local_sb: dict = {}
        _run_code(code, local_sb)
        return local_sb

    # Phase 1: exec the candidate code with a 3s budget
    exec_result, timed_out, exc = _with_timeout(_do_exec, 3.0)
    if timed_out:
        return 0, len(tests), ["<exec timeout>"] * len(tests)
    if exc is not None:
        return 0, len(tests), [f"<compile/exec error: {exc!r}>"] * len(tests)
    sandbox = exec_result or {}

    if entry_point and entry_point not in sandbox:
        return 0, len(tests), [f"<missing entry point {entry_point!r}>"] * len(tests)

    # Phase 2: evaluate each assertion with a 1s budget, 7s total
    failed: list[str] = []
    passing = 0
    deadline = time.time() + 7.0
    for assertion in tests:
        remaining = deadline - time.time()
        if remaining <= 0:
            failed.append(f"{assertion}  -> <budget exhausted>")
            continue
        per_call_timeout = min(1.0, remaining)

        def _do_eval(_a=assertion):
            return bool(_eval_expr(_a, sandbox))

        val, timed_out, exc = _with_timeout(_do_eval, per_call_timeout)
        if timed_out:
            failed.append(f"{assertion}  -> <timeout>")
            continue
        if exc is not None:
            failed.append(f"{assertion}  -> {exc!r}")
            continue
        if val:
            passing += 1
        else:
            failed.append(assertion)
    return passing, len(tests), failed


def main() -> None:
    payload = json.loads(sys.stdin.read())
    n_pass, n_total, failed = run_tests(
        payload["code"], payload.get("entry_point", ""), list(payload.get("tests", []))
    )
    sys.stdout.write(json.dumps({"n_passing": n_pass, "n_total": n_total, "failed": failed}))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
