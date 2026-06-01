"""Zero-spend, mock-mode end-to-end validation of the Bench v2 harness.

Exercises every component without a single API call:
  - the fixed verify-revise loop on the in-code SQLite fixture,
  - the SQL execution oracle (correct / wrong-result / error / order-insensitivity),
  - all frozen metrics (found-it-then-broke-it, overshoot, oscillation, divergence),
  - the strong-oracle re-confirmation gate,
  - the hard spend-cap abort path.

Run:  .venv/bin/python -m bench_v2.selftest
Exit code 0 == harness is wired correctly and ready for a (gated, paid) real run.
"""
from __future__ import annotations

import sys

from . import metrics, oracle
from .data import Task, mock_tasks
from .runner import SpendCapExceeded, run_tier

CORRECT = {
    "mock-engineers": "SELECT name FROM employees WHERE dept='Engineering'",
    "mock-topsalary": "SELECT name FROM employees ORDER BY salary DESC LIMIT 1",
    "mock-salesloc": "SELECT DISTINCT d.location FROM depts d JOIN employees e "
                     "ON d.dept=e.dept WHERE e.dept='Sales'",
}

# Scripted per-task trajectories (one SQL per iteration), max_iter=6.
SCRIPTS = {
    # found-it-then-broke-it + oscillation: wrong, RIGHT, wrong(cycle), RIGHT, error, wrong(terminal)
    "mock-engineers": [
        "SELECT name FROM employees WHERE dept='Sales'",
        CORRECT["mock-engineers"],
        "SELECT name FROM employees WHERE dept='Sales'",
        CORRECT["mock-engineers"],
        "SELECT nope FROM employees",
        "SELECT name FROM employees WHERE dept='Ops'",
    ],
    # clean: wrong then correct, stays correct (reached good and KEPT it)
    "mock-topsalary": [
        "SELECT name FROM employees ORDER BY salary ASC LIMIT 1",
        CORRECT["mock-topsalary"],
        CORRECT["mock-topsalary"],
        CORRECT["mock-topsalary"],
    ],
    # never correct (wrong results + a syntax error) -> excluded from FTB denominator
    "mock-salesloc": [
        "SELECT location FROM depts",
        "SELECT location FROM depts WHERE dept='Ops'",
        "SELECT bad syntax(",
        "SELECT location FROM depts WHERE dept='Engineering'",
    ],
}

PASS, FAIL = "\033[92mPASS\033[0m", "\033[91mFAIL\033[0m"
_failures = 0


def check(label, cond):
    global _failures
    print(f"  [{PASS if cond else FAIL}] {label}")
    if not cond:
        _failures += 1


def test_metrics_units():
    print("metrics unit cases:")
    m = metrics.trial_metrics([1, 0, 1, 0, 1, 1], ["A", "G", "A", "G", "E", "O"])
    check("found_then_broke fires (reached good then terminal wrong)", m.found_then_broke)
    check("overshoot fires (s rose above best)", m.overshoot)
    check("oscillation fires (result cycle A,G,A)", m.oscillation)
    check("divergence does NOT fire (oscillating, not trailing-wrong)", not m.divergence)

    m2 = metrics.trial_metrics([1, 0, 0, 0], ["A", "G", "G", "G"])
    check("clean converge: not found_then_broke", not m2.found_then_broke)
    check("clean converge: reached_good", m2.reached_good)
    check("clean converge: no overshoot", not m2.overshoot)

    m3 = metrics.trial_metrics([1, 1, 1, 1], ["A", "B", "E", "C"])
    check("never-correct: not reached_good", not m3.reached_good)
    check("never-correct: not found_then_broke", not m3.found_then_broke)

    m4 = metrics.trial_metrics([1, 0, 1, 1, 1], ["A", "G", "X", "Y", "Z"])
    check("divergence fires (correct then 3+ trailing wrong, no cycle)", m4.divergence)


def test_oracle():
    print("oracle (SQL execution on mock DB):")
    tasks = {t.task_id: t for t in mock_tasks()}
    eng = tasks["mock-engineers"]
    ok_correct, _ = oracle.matches(CORRECT["mock-engineers"], eng)
    ok_wrong, _ = oracle.matches("SELECT name FROM employees WHERE dept='Sales'", eng)
    ok_err, res_err = oracle.matches("SELECT nope FROM employees", eng)
    check("correct query matches gold", ok_correct)
    check("wrong-result query does not match", not ok_wrong)
    check("erroring query: not match and exec_ok False", (not ok_err) and (not res_err.ok))
    # order-insensitivity: ORDER BY must NOT count as a difference
    ok_ordered, _ = oracle.matches(CORRECT["mock-engineers"] + " ORDER BY name DESC", eng)
    check("ORDER BY variant still matches (order-insensitive oracle)", ok_ordered)

    # reconfirm gate: genuine degrade confirmed; false degrade (still-correct terminal) rejected
    confirmed = oracle.reconfirm(CORRECT["mock-engineers"],
                                 "SELECT name FROM employees WHERE dept='Ops'", eng)
    rejected = oracle.reconfirm(CORRECT["mock-engineers"],
                                CORRECT["mock-engineers"] + " ORDER BY name DESC", eng)
    check("reconfirm CONFIRMS a genuine degrade", confirmed)
    check("reconfirm REJECTS a false (order-only) degrade", not rejected)


def test_end_to_end():
    print("end-to-end mock run (run_tier):")
    tasks = mock_tasks()
    result = run_tier(tasks, "mock", None, max_iter=6, max_spend=80.0, mock_scripts=SCRIPTS)
    agg = result["aggregate"]
    check("n == 3", result["n"] == 3)
    check("cost == $0 (mock)", result["cost_usd"] == 0.0)
    check("reached_good == 2 (engineers, topsalary)", agg["n_reached_good"] == 2)
    check("found_then_broke == 1 (engineers)", agg["found_then_broke"] == 1)
    check("ftb reconfirmed == 1", result["ftb_confirmed"] == 1)
    check("ftb reconfirm-rejected == 0", result["ftb_rejected_by_reconfirm"] == 0)
    check("overshoot == 1", agg["overshoot"] == 1)
    check("oscillation == 1", agg["oscillation"] == 1)
    check("ftb_rate == 0.5 (1 of 2 reached-good)", abs(result["ftb_rate"] - 0.5) < 1e-9)
    check("verdict computed", "HEADLINE" in result["verdict"])


def test_spend_cap():
    print("spend-cap abort path:")
    tasks = mock_tasks()
    raised = False
    try:
        # negative cap => any spend (even $0) trips the guard after the first task
        run_tier(tasks, "mock", None, max_iter=2, max_spend=-1.0, mock_scripts=SCRIPTS)
    except SpendCapExceeded:
        raised = True
    check("SpendCapExceeded raised when cost exceeds cap", raised)


def main():
    print("=" * 64)
    print("Bench v2 — mock-mode self-test (zero spend, no network)")
    print("=" * 64)
    test_metrics_units()
    test_oracle()
    test_end_to_end()
    test_spend_cap()
    print("-" * 64)
    if _failures:
        print(f"{_failures} check(s) FAILED")
        sys.exit(1)
    print("ALL CHECKS PASSED — harness wired correctly, ready for a gated paid run.")
    sys.exit(0)


if __name__ == "__main__":
    main()
