"""SQL execution oracle for Bench v2.

The error signal is BIRD-style execution accuracy: a prediction is correct iff
its result set matches the gold query's result set. Comparison is
order-normalized by default (set-of-rows equality) to avoid counting a mere
ORDER BY difference as a degrade — this is the verifier-noise control the
prereg (§5) requires for SQL.

``reconfirm()`` re-executes a (best, terminal) pair under the strongest
comparison so a found-it-then-broke-it event is only counted when the degrade
is real and not flaky execution.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional

from .data import Task

_EXEC_TIMEOUT_S = 5.0


@dataclass
class ExecResult:
    ok: bool                       # did the SQL execute without error?
    rows: Optional[frozenset]      # normalized result (set of row tuples) or None on error
    error: str                     # execution error text ("" if ok)


def _normalize(rows, order_sensitive: bool):
    # rows: list of tuples. Normalize cell types to str for robust comparison
    # across int/float/decimal quirks; set-of-rows unless order_sensitive.
    norm = [tuple("" if c is None else str(c) for c in row) for row in rows]
    return tuple(norm) if order_sensitive else frozenset(norm)


def execute(sql: str, db_path: str, order_sensitive: bool = False) -> ExecResult:
    if not sql or not sql.strip():
        return ExecResult(False, None, "empty query")
    try:
        con = sqlite3.connect(db_path, timeout=_EXEC_TIMEOUT_S)
        con.execute(f"PRAGMA busy_timeout = {int(_EXEC_TIMEOUT_S * 1000)}")
        cur = con.execute(sql)
        rows = cur.fetchall()
        norm = _normalize(rows, order_sensitive)
        if order_sensitive:
            norm = frozenset({norm})  # keep type uniform; rarely used
        return ExecResult(True, norm if not order_sensitive else frozenset(norm), "")
    except Exception as e:  # noqa: BLE001 — any SQL/exec error is a failed attempt
        return ExecResult(False, None, f"{type(e).__name__}: {e}")
    finally:
        try:
            con.close()
        except Exception:
            pass


def matches(pred_sql: str, task: Task, order_sensitive: bool = False) -> tuple[bool, ExecResult]:
    """True iff pred_sql's result set equals the gold query's result set."""
    gold = execute(task.gold_sql, task.db_path, order_sensitive)
    pred = execute(pred_sql, task.db_path, order_sensitive)
    if not pred.ok or not gold.ok:
        return False, pred
    return (pred.rows == gold.rows), pred


def feedback(pred_sql: str, exec_result: ExecResult, is_correct: bool, task: Task) -> str:
    """Verifier feedback string fed back to the model for the next revision.

    Two regimes (this is what lets the loop iterate PAST success and exposes
    the found-it-then-broke-it failure mode, per prereg §3):
      - wrong: report the execution error or the result mismatch.
      - correct: a generic "review and improve" critique (no error to fix) —
        models a refinement loop that does not know it is already done.
    """
    if not is_correct:
        if not exec_result.ok:
            return f"Your query raised an error: {exec_result.error}. Fix the SQL."
        return ("Your query executed but returned the wrong result set for the question. "
                "Reconsider the joins/filters/aggregation and try again.")
    return ("Your query returned a result. Review it for correctness and clarity and produce "
            "an improved final version of the SQL.")


def reconfirm(best_sql: str, terminal_sql: str, task: Task) -> bool:
    """Strong-oracle re-confirmation of a found-it-then-broke-it event.

    Returns True iff, on re-execution, the best query genuinely matches gold AND
    the terminal query genuinely does not — under order-insensitive set equality.
    Guards against flaky/ORDER-BY false degrades (prereg §5).
    """
    best_ok, _ = matches(best_sql, task, order_sensitive=False)
    term_ok, _ = matches(terminal_sql, task, order_sensitive=False)
    return bool(best_ok and not term_ok)
