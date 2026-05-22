"""Shared base for W1 code-gen workloads.

Both W1-a (LangGraph) and W1-b (Claude Agent SDK) inherit this base — they
differ only in the `framework` field. The iteration body asks the model to
write Python that satisfies an assertion-test suite; failing assertions
are fed back as the next iteration's prompt.

Error: number of failing tests (so error=0 means all tests pass).
Programmatic quality: pass rate (n_passing / n_total) in [0, 1].
"""

from __future__ import annotations

import hashlib
import random
import re
from typing import Optional

from ...llm import Completion
from ...workload import IterationOutcome, TrialInput, Workload
from . import in_mock_mode
from .codegen_problems import PROBLEMS
from .framework_invoke import invoke


def _extract_code(text: str) -> str:
    """Pull the first ```python ... ``` block (or any fenced block) from text;
    fall back to the whole text. Strips fences only — keeps everything else
    intact so syntax-error feedback is informative."""
    m = re.search(r"```(?:python|py)?\s*\n(.*?)\n```", text or "", re.S)
    if m:
        return m.group(1).strip()
    return (text or "").strip()


def _run_tests(code: str, entry_point: str, tests: list[str]) -> tuple[int, int, list[str]]:
    """Exec the candidate code in a sandbox dict, then evaluate each assertion.

    Budget: 10s total wall-clock per call (covers exec + all assertions).
    If exec exceeds 3s on its own, the whole call fails. Per-assertion
    timeout is min(1s, remaining_budget). Once budget exhausted, remaining
    assertions are counted as failing (without running) so the harness
    doesn't burn unbounded time on pathological LLM code.

    Returns (n_passing, n_total, failed_messages). Compile + runtime errors
    on the code itself count as ALL tests failing.

    The sandbox is a fresh dict; builtins are available but no filesystem/
    network isolation — we rely on Anthropic's RLHF to not have the model
    write filesystem-destructive code for these algorithmic toys. Documented
    as a limitation in the writeup.
    """
    import signal as _signal
    import time as _time

    class _Timeout(Exception):
        pass

    def _alarm(_signum, _frame):
        raise _Timeout("budget exceeded")

    sandbox: dict = {}
    prev_handler = _signal.signal(_signal.SIGALRM, _alarm)
    deadline = _time.time() + 10.0  # total budget for this call
    try:
        _signal.alarm(3)
        try:
            exec(code, sandbox)
        except _Timeout:
            return 0, len(tests), ["<exec timeout>"] * len(tests)
        except Exception as exc:
            return 0, len(tests), [f"<compile/exec error: {exc!r}>"] * len(tests)
        finally:
            _signal.alarm(0)

        if entry_point and entry_point not in sandbox:
            return 0, len(tests), [f"<missing entry point {entry_point!r}>"] * len(tests)

        failed: list[str] = []
        passing = 0
        for assertion in tests:
            remaining = deadline - _time.time()
            if remaining <= 0:
                # Out of total budget — count remaining as failed without running
                failed.append(f"{assertion}  -> <budget exhausted>")
                continue
            # Per-assertion timeout: 1s or remaining budget, whichever is less
            budget = max(1, min(1, int(remaining + 0.5)))
            try:
                _signal.alarm(budget)
                ok = bool(eval(assertion, sandbox))
            except _Timeout:
                ok = False
                failed.append(f"{assertion}  -> <timeout>")
                continue
            except Exception as exc:
                ok = False
                failed.append(f"{assertion}  -> {exc!r}")
                continue
            finally:
                _signal.alarm(0)
            if ok:
                passing += 1
            else:
                failed.append(assertion)
        return passing, len(tests), failed
    finally:
        _signal.signal(_signal.SIGALRM, prev_handler)


class CodegenWorkload(Workload):
    """Base class for W1 cells (LangGraph + Claude Agent SDK).

    Subclasses must override `id` and `framework`. Model is Haiku 4.5 per
    BENCH_PROTOCOL.md §"Models".
    """

    model = "claude-haiku-4-5"
    loop_type = "verify_revise"
    target_error = 0.0  # all tests passing
    # Lockdown 2a: tie "better" explicitly to the programmatic metric.
    task_description = (
        "Write a Python function that satisfies a specification, verified by "
        "a suite of assertion-style unit tests. A better attempt passes more "
        "of the unit tests with no spurious side effects (no global state "
        "mutation, no exceptions raised on the test inputs)."
    )

    # Set by subclasses
    id: str = "w1-codegen-base"
    framework: str = "bare-anthropic"

    # Module-level deterministic shuffle of problem indices. Seeded with 0
    # (locked, NOT seed-dependent) so the same shuffle order is reproducible
    # across runs. Decouples problem-id ordering (which correlates with
    # difficulty in MBPP/HumanEval — smaller IDs are usually easier) from
    # the bench's seed-based selection. This is NOT difficulty curation
    # (no problem is filtered out, all participate uniformly).
    _shuffled_indices: Optional[list[int]] = None

    @classmethod
    def _problem_index_for_seed(cls, seed: int) -> int:
        if cls._shuffled_indices is None:
            n = len(PROBLEMS)
            order = list(range(n))
            random.Random(0).shuffle(order)
            cls._shuffled_indices = order
        return cls._shuffled_indices[seed % len(cls._shuffled_indices)]

    def generate_trial(self, seed: int) -> TrialInput:
        idx = self._problem_index_for_seed(seed)
        problem = PROBLEMS[idx]
        rng = random.Random(seed)
        # Shuffle test order so trials on the same problem differ in feedback
        # priority but converge on the same correctness target.
        tests = list(problem["tests"])
        rng.shuffle(tests)
        return TrialInput(
            seed=seed,
            prompt=problem["prompt"],
            initial_state={
                "name": problem["name"],
                "entry_point": problem["entry_point"],
                "tests": tests,
                "spec": problem["prompt"],
            },
            metadata={
                "problem_idx": idx,
                "problem_name": problem["name"],
                "problem_hash": hashlib.sha256(problem["name"].encode()).hexdigest()[:12],
                "n_tests": len(tests),
            },
        )

    def run_iteration(
        self,
        trial: TrialInput,
        prev_output: Optional[str],
        iteration: int,
        llm,
    ) -> IterationOutcome:
        spec = trial.initial_state["spec"]
        entry_point = trial.initial_state["entry_point"]
        tests = trial.initial_state["tests"]
        if iteration == 1:
            # HumanEval+-style prompt: show the signature+docstring, ask for
            # the body. We pass the prompt verbatim from the dataset and just
            # add formatting instructions.
            prompt = (
                f"Complete the following Python function:\n\n"
                f"```python\n{spec}```\n\n"
                f"Return ONLY the complete function (signature + body) inside "
                f"a ```python fenced code block. Do not include tests or "
                f"example calls. The function must be named `{entry_point}`."
            )
        else:
            prev_code = _extract_code(prev_output or "")
            n_pass, n_total, failed = _run_tests(prev_code, entry_point, tests)
            failed_lines = "\n".join(f"  - {f}" for f in failed[:5])
            prompt = (
                f"Your previous attempt failed {n_total - n_pass} of {n_total} "
                f"tests. Sample failing assertions:\n{failed_lines}\n\n"
                f"Rewrite the function to fix the failing tests. Return ONLY "
                f"the complete function definition inside a ```python fenced "
                f"code block. The function must be named `{entry_point}`.\n\n"
                f"Spec for reference:\n```python\n{spec}```"
            )

        comp = invoke(self.framework, llm, prompt, max_tokens=600)
        text = comp.text or ""
        code = _extract_code(text)
        n_pass, n_total, _failed = _run_tests(code, entry_point, tests)
        # Error = number of failing tests; lower is better; 0 = all pass.
        error_val = float(n_total - n_pass) if n_total > 0 else float("inf")
        # In mock mode the LLM returns synthetic strings that won't parse —
        # report worst-case so LoopGain still observes a meaningful trajectory.
        if in_mock_mode():
            # Synthesize a converging trajectory: mock starts high, drops over iters
            from math import floor
            error_val = float(max(0, n_total - floor(iteration)))
        return IterationOutcome(
            output=text,  # full LLM text (includes fences) — _extract_code is re-applied next iter
            completion=comp,
            error=error_val,
        )

    def error_fn(self, output: str) -> float:  # noqa: D401 — required by ABC
        """Compute failing-test count from an LLM output string."""
        # The signature requires a single-arg form, but this workload's error
        # is computed inside run_iteration where we still have trial context.
        # Provided here for API completeness; returns 0 if output is empty.
        return 0.0 if not output else 1.0

    def programmatic_quality(self, output: str) -> Optional[float]:
        # Without trial context here we can't run tests; this method is called
        # by analysis after the fact and would need the trial reference. The
        # bench logs pass rate per-iteration in the raw JSONL (via final_error
        # vs n_tests) so analysis derives quality from the trial result, not
        # from this hook. Returning None signals "see raw trial result".
        return None
