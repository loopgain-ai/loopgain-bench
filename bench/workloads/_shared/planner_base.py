"""Shared base for W3 planner-executor workloads.

W3-a (OpenAI Agents SDK, GPT-4.1-mini) and W3-b (LangGraph, Claude Sonnet
4.6) inherit this base. The task is BFCL v4 'multiple' (Berkeley Function
Calling Leaderboard): single-turn function-calling where the model picks
from a list of candidate functions and provides correct argument values.
Grading is programmatic — see `_shared/bfcl_tasks.py::grade_call`.

Error per iteration:
  - 0 if the call matches ground-truth (success)
  - 5 if no parseable call extracted
  - >=1 otherwise (1 for wrong function + 1 per wrong param)

Programmatic quality: 1.0 if success, 0.0 otherwise.
"""

from __future__ import annotations

import hashlib
import json
from typing import Optional

from ...workload import IterationOutcome, TrialInput, Workload
from . import in_mock_mode
from .bfcl_tasks import TASKS, grade_call
from .framework_invoke import invoke


class PlannerWorkload(Workload):
    """Base class for W3 cells. Model + framework set by subclasses."""

    loop_type = "tool_use_retry"
    target_error = 0.0
    # Lockdown 2a: tie "better" to BFCL's programmatic grade.
    task_description = (
        "Given a user query and a list of candidate functions with parameter "
        "schemas, output the correct function call (name + parameter values) "
        "in JSON format. A better attempt picks the right function and "
        "provides parameter values that exactly match what the task requires "
        "(graded against the published BFCL ground-truth)."
    )

    id: str = "w3-planner-base"
    framework: str = "bare-openai"
    model: str = "gpt-4.1-mini"

    def generate_trial(self, seed: int) -> TrialInput:
        task = TASKS[seed % len(TASKS)]
        # Flatten multi-turn questions into a single user prompt (BFCL "multiple"
        # is single-turn; this is robust to occasional multi-turn entries).
        question_text = "\n".join(
            turn.get("content", "")
            for turn_list in task["question"]
            for turn in turn_list
            if turn.get("role") == "user"
        )
        return TrialInput(
            seed=seed,
            prompt=question_text,
            initial_state={
                "task_id": task["id"],
                "question": question_text,
                "functions": task["functions"],
                "ground_truth": task["ground_truth"],
            },
            metadata={
                "task_idx": seed % len(TASKS),
                "task_id": task["id"],
                "task_hash": hashlib.sha256(task["id"].encode()).hexdigest()[:12],
                "n_candidate_functions": len(task["functions"]),
            },
        )

    def _format_functions(self, functions: list[dict]) -> str:
        """Render the candidate functions in a compact, human-readable spec."""
        parts: list[str] = []
        for fn in functions:
            name = fn.get("name", "?")
            desc = fn.get("description", "")
            params = fn.get("parameters", {}).get("properties", {})
            required = set(fn.get("parameters", {}).get("required", []))
            param_lines = []
            for pname, pspec in params.items():
                req = " (required)" if pname in required else ""
                ptype = pspec.get("type", "any")
                pdesc = pspec.get("description", "")
                param_lines.append(f"    - {pname}: {ptype}{req} — {pdesc}")
            parts.append(f"  - {name}: {desc}\n" + "\n".join(param_lines))
        return "\n".join(parts)

    def _build_prompt(self, trial: TrialInput, prev_output: Optional[str], iteration: int) -> str:
        question = trial.initial_state["question"]
        functions = trial.initial_state["functions"]
        fn_spec = self._format_functions(functions)
        if iteration == 1:
            return (
                f"User query:\n{question}\n\n"
                f"Candidate functions:\n{fn_spec}\n\n"
                f"Select the appropriate function and provide its arguments. "
                f"Return ONLY a JSON object of the form "
                f"`{{\"name\": \"...\", \"args\": {{...}}}}` inside a "
                f"```json fenced code block. No preamble, no explanation."
            )
        # iter 2+: feedback
        ok, err, debug = grade_call(prev_output or "", trial.initial_state["ground_truth"])
        debug_str = json.dumps(debug, default=str)[:300]
        return (
            f"Your previous attempt did not match the expected call. Debug: {debug_str}\n\n"
            f"User query:\n{question}\n\n"
            f"Candidate functions:\n{fn_spec}\n\n"
            f"Try again. Return ONLY a JSON object of the form "
            f"`{{\"name\": \"...\", \"args\": {{...}}}}` inside a ```json "
            f"fenced code block."
        )

    def run_iteration(
        self,
        trial: TrialInput,
        prev_output: Optional[str],
        iteration: int,
        llm,
    ) -> IterationOutcome:
        prompt = self._build_prompt(trial, prev_output, iteration)
        comp = invoke(self.framework, llm, prompt, max_tokens=400)
        text = comp.text or ""
        ok, err, _debug = grade_call(text, trial.initial_state["ground_truth"])
        if in_mock_mode():
            err = float(max(0.0, 3.0 - iteration))
        return IterationOutcome(output=text, completion=comp, error=err)

    def error_fn(self, output: str) -> float:
        return 0.0

    def programmatic_quality(self, output: str) -> Optional[float]:
        return None
