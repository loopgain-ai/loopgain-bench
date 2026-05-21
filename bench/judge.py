"""LLM-judge for pairwise quality comparison.

Per BENCH_PROTOCOL.md methodology lockdown #2: judge model ≠ loop model.
Per lockdown #3: pairwise position is randomized (LoopGain output in position
A in 50% of comparisons, B in the other 50%), seeded deterministically per
trial so the comparison is reproducible.

This module exposes one entry point: `pairwise_winrate`. It takes two
collections of (trial_id, output) pairs — one for LoopGain, one for B20 —
runs each pair through a judge, and reports the proportion of comparisons
where LoopGain's output is preferred (ties = 0.5).

Implementation note: the judge prompt is INTENTIONALLY single-shot and short.
Long judge prompts increase variance. The cross-model rotation (Sonnet ↔ GPT
↔ Haiku) is what mitigates judge bias, not a sophisticated prompt.

This file is a contract + stub. The full implementation is part of the
Claude Code implementation session; the stub below documents the shape so
analysis/run.py can be written against it now.
"""

from __future__ import annotations

import os
import random
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class PairwiseComparison:
    trial_id: str
    output_a: str  # the output placed in position A (randomized)
    output_b: str
    lg_position: str  # 'A' or 'B' — which position LoopGain was in
    judge_choice: str  # 'A', 'B', or 'TIE'
    judge_model: str
    judge_rationale: str  # brief, for spot-checking; never used in aggregate metrics


JUDGE_PROMPT_TEMPLATE = """You are evaluating two attempts at the same task. Read both carefully, then output ONE of: A, B, or TIE.

TASK:
{task_description}

ATTEMPT A:
{output_a}

ATTEMPT B:
{output_b}

Output exactly one letter (A, B, or TIE) followed by one sentence of reasoning. No preamble."""


def pairwise_winrate(
    lg_outputs: dict[str, str],
    b20_outputs: dict[str, str],
    task_description: str,
    judge_model: str,
    *,
    seed: int = 0,
) -> dict:
    """Run pairwise judgments and return aggregate stats.

    Args:
        lg_outputs:  {trial_id: output_string} for LoopGain condition
        b20_outputs: {trial_id: output_string} for B20 condition
        task_description: one-line description of what the task was (for judge context)
        judge_model: model id; MUST be different from the loop model. Enforced here.
        seed: deterministic position-randomization seed

    Returns:
        {
            "n_comparisons": int,
            "lg_wins": int,
            "b20_wins": int,
            "ties": int,
            "winrate_lg": float,  # (lg_wins + 0.5 * ties) / n
            "judge_model": str,
            "comparisons": list[PairwiseComparison],  # for raw audit
        }

    Implementation note: this function is the contract. The body below is a
    stub that the implementation session fills in with the real LLM judge
    call. See `bench/llm.py` for client construction; recommended judge models
    are cross-vendor (e.g. judge Anthropic loop outputs with GPT-4.1-mini and
    vice versa).
    """
    raise NotImplementedError(
        "pairwise_winrate is a stub. Implement in the Claude Code session; "
        "see bench/judge.py docstring and BENCH_PROTOCOL.md §Metrics."
    )


def _enforce_cross_model(loop_model: str, judge_model: str) -> None:
    """Lockdown #2: judge model MUST differ from loop model. Raise loudly."""
    if loop_model == judge_model:
        raise ValueError(
            f"Methodology violation: judge model {judge_model!r} == loop model. "
            f"BENCH_PROTOCOL.md methodology lockdown #2 forbids same-model judging."
        )
    # Same vendor family is also flagged but allowed with a warning
    # (e.g. Sonnet judging Haiku is suboptimal but not invalid).
    if loop_model.startswith("claude-") and judge_model.startswith("claude-"):
        print(
            f"  [judge warning] both loop ({loop_model}) and judge ({judge_model}) "
            f"are Anthropic family. Prefer cross-vendor."
        )
