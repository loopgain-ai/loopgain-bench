"""Shared base for W2 critique-revise debate workloads.

W2-a (AutoGen) and W2-b (CrewAI) inherit this base; they differ only in the
`framework` field. The iteration body asks the model to argue for or against
a claim with citation + counter-argument; iter 2+ feeds back rubric failures
("missing citation," "no counter-argument," "word count outside 120-180").
Model: GPT-4.1-mini per BENCH_PROTOCOL.md §"Models".

Error: 4 - rubric_score (max 4, min 0; lower error is better).
Programmatic quality: rubric_score / 4 (in [0, 1]).
"""

from __future__ import annotations

import hashlib
import random
from typing import Optional

from ...workload import IterationOutcome, TrialInput, Workload
from . import in_mock_mode
from .debate_topics import TOPICS, score_against_rubric
from .framework_invoke import invoke


class DebateWorkload(Workload):
    """Base class for W2 cells (AutoGen + CrewAI). Model: GPT-4.1-mini."""

    model = "gpt-4.1-mini"
    loop_type = "critique_revise"
    target_error = 0.0
    # Lockdown 2a: tie "better" to the rubric metric.
    task_description = (
        "Write a short argumentative response (120-180 words) for or against "
        "a stated claim. A better attempt scores higher on a 4-point rubric: "
        "word count in [120, 220], cites a specific named source, explicitly "
        "names and rebuts a counter-argument, and includes a concrete number "
        "(year, percentage, study size)."
    )

    id: str = "w2-debate-base"
    framework: str = "bare-openai"

    def generate_trial(self, seed: int) -> TrialInput:
        topic = TOPICS[seed % len(TOPICS)]
        rng = random.Random(seed)
        # Side is chosen deterministically — alternating per seed within a topic
        # so trials on the same topic differ in stance.
        side = "for" if rng.random() < 0.5 else "against"
        return TrialInput(
            seed=seed,
            prompt=topic["prompt"],
            initial_state={
                "topic": topic["name"],
                "claim_prompt": topic["prompt"],
                "rubric": topic["rubric"],
                "side": side,
            },
            metadata={
                "topic_idx": seed % len(TOPICS),
                "topic_name": topic["name"],
                "topic_hash": hashlib.sha256(topic["name"].encode()).hexdigest()[:12],
                "side": side,
            },
        )

    def run_iteration(
        self,
        trial: TrialInput,
        prev_output: Optional[str],
        iteration: int,
        llm,
    ) -> IterationOutcome:
        claim_prompt = trial.initial_state["claim_prompt"]
        side = trial.initial_state["side"]
        rubric = trial.initial_state["rubric"]
        if iteration == 1:
            prompt = f"Take the stance '{side}' on the following claim and write the argument.\n\n{claim_prompt}"
        else:
            score, max_score, failed = score_against_rubric(prev_output or "", rubric)
            failed_lines = "\n".join(f"  - {f}" for f in failed) or "  - (none — re-check overall quality)"
            prompt = (
                f"Your previous attempt scored {score}/{max_score} on the rubric. "
                f"Failed checks:\n{failed_lines}\n\n"
                f"Revise the response to fix the failing rubric items. Take the same "
                f"stance ('{side}'). Original claim:\n\n{claim_prompt}"
            )
        comp = invoke(self.framework, llm, prompt, max_tokens=400)
        text = comp.text or ""
        score, max_score, _failed = score_against_rubric(text, rubric)
        error_val = float(max_score - score)
        if in_mock_mode():
            # Synthetic converging trajectory for mock-mode
            error_val = float(max(0, max_score - iteration))
        return IterationOutcome(output=text, completion=comp, error=error_val)

    def error_fn(self, output: str) -> float:
        # For consistency with the base contract. Real error is computed in
        # run_iteration where we have rubric context.
        return 0.0

    def programmatic_quality(self, output: str) -> Optional[float]:
        # See codegen_base.programmatic_quality note — analysis derives quality
        # from raw trial result per-iteration; this hook is informational.
        return None
