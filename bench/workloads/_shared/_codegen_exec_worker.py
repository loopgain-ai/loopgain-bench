"""Subprocess worker: execute model-generated candidate code + assertions in
process ISOLATION so a pathological GIL-holding compile() can be hard-killed by
the parent (subprocess timeout -> SIGKILL) instead of wedging the bench runner.

WHY THIS EXISTS
---------------
The previous in-process guard (`_with_timeout`: daemon thread + join(timeout))
cannot interrupt a `compile()` of a pathologically-nested expression:
`_PyAST_Compile`/`_PySymtable_Build` is one long C call that holds the GIL and
never yields. `join()` returns (caller thinks it timed out) but the leaked
daemon thread keeps the GIL forever and starves every other thread in the
process. A single bad W1 trial froze the entire registered run. Per
RUNNING_BENCHMARKS.md §6 ("execute model-generated code sandboxed") and §2.2
("hard wall-clock bound"), candidate code now runs in this child process and
the parent enforces a real wall-clock kill that a held GIL cannot evade.

Inner semantics are IDENTICAL to the old codegen_base._run_tests (3s exec
budget; 1s per assertion, 7s total) so pass/fail counts are unchanged for
well-behaved code. The daemon-thread guards here still give per-assertion
granularity for the common case; the parent's outer SIGKILL is the backstop
for the GIL-holding case the inner guards cannot catch.

Protocol: reads JSON {"code","entry_point","tests"} on stdin, writes JSON
{"n_passing","n_total","failed"} on stdout. Stdlib only.
"""
from __future__ import annotations

import builtins
import json
import sys
import threading
import time

# Python's exec/eval builtins, hoisted to locals. This worker IS the sandbox:
# it runs untrusted model-generated code on purpose, isolated in a child process
# the parent can SIGKILL. (Not shell exec / command-injection — this is the
# Python `exec`/`eval` interpreter primitive, the documented bench design.)
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
