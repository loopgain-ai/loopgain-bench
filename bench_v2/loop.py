"""The fixed verify-revise loop driver for Bench v2.

CRITICAL DESIGN POINT (prereg §3): to *measure* how often a loop iterates past
success and degrades, the loop must NOT short-circuit when it first succeeds —
it runs to a fixed ``max_iter`` (mirroring the v1 B20 condition). We record the
iteration of the first success separately. found-it-then-broke-it is then
"first success happened at t* < terminal AND the terminal output is wrong."

A single fixed harness is used for every model and task, so framework is
eliminated as a confound (the v1 re-analysis showed adapters differ in loop
dynamics).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Optional

from . import oracle
from .data import Task
from .llm import Provider, extract_sql

_SYSTEM = (
    "You are an expert SQLite analyst. Given a database schema and a question, "
    "write a single SQLite SELECT query that answers it. Return ONLY the SQL in a "
    "```sql code block."
)


def _result_hash(res: "oracle.ExecResult") -> str:
    if not res.ok or res.rows is None:
        return "ERR"
    return hashlib.sha1(repr(sorted(res.rows)).encode()).hexdigest()[:12]


@dataclass
class IterPoint:
    iter: int
    sql: str
    s: int                 # error signal: 0 = correct (matches gold), 1 = wrong
    exec_ok: bool
    error: str
    result_hash: str


@dataclass
class TrialResult:
    task_id: str
    difficulty: Optional[str]
    trajectory: list[IterPoint] = field(default_factory=list)
    first_success_iter: Optional[int] = None
    best_iter: Optional[int] = None     # earliest iter with s==0 (== first_success here)
    terminal_s: int = 1
    input_tokens: int = 0
    output_tokens: int = 0

    def s_series(self) -> list[int]:
        return [p.s for p in self.trajectory]


def run_loop(
    task: Task,
    provider: Provider,
    max_iter: int = 10,
    stop_at_success: bool = False,
) -> TrialResult:
    """Run one task through the fixed verify-revise loop.

    stop_at_success=False (default, the measurement mode) keeps iterating after
    a correct answer using a generic "review and improve" critique, so degrade
    events can be observed. Set True to model a target-met short-circuit loop
    (used only for the Stage-3 comparison, not the base-rate measurement).
    """
    res = TrialResult(task_id=task.task_id, difficulty=task.difficulty)
    feedback_msg: Optional[str] = None
    in0, out0 = provider.usage.input_tokens, provider.usage.output_tokens

    for t in range(max_iter):
        user = _build_user_prompt(task, feedback_msg)
        raw = provider.complete(_SYSTEM, user)
        sql = extract_sql(raw)
        is_correct, exec_result = oracle.matches(sql, task)
        s = 0 if is_correct else 1
        res.trajectory.append(
            IterPoint(t, sql, s, exec_result.ok, exec_result.error, _result_hash(exec_result))
        )
        if s == 0 and res.first_success_iter is None:
            res.first_success_iter = t
            res.best_iter = t
        if stop_at_success and s == 0:
            break
        feedback_msg = oracle.feedback(sql, exec_result, is_correct, task)

    res.terminal_s = res.trajectory[-1].s if res.trajectory else 1
    res.input_tokens = provider.usage.input_tokens - in0
    res.output_tokens = provider.usage.output_tokens - out0
    return res


def _build_user_prompt(task: Task, feedback_msg: Optional[str]) -> str:
    parts = [f"Schema:\n{task.schema_ddl}"]
    if task.evidence:
        parts.append(f"\nExternal knowledge: {task.evidence}")
    parts.append(f"\nQuestion: {task.question}")
    if feedback_msg:
        parts.append(f"\nFeedback on your previous attempt: {feedback_msg}")
    return "\n".join(parts)
