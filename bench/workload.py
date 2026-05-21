"""Workload abstract base class.

A Workload is one (task × framework × model) cell of the bench matrix. It
generates trial prompts deterministically from a seed, runs one revise
iteration given prior output, computes an error signal on the output, and
optionally provides a programmatic quality score against a public benchmark.

The base class enforces the methodology lockdowns from BENCH_PROTOCOL.md:

- Seeded determinism: `generate_trial(seed)` must be a pure function of seed.
- No mid-run filtering: a workload may NEVER reject a trial after
  `generate_trial` returns; the runner reports failed trials, never drops them.
- Honest token accounting: workloads do not manipulate Completion fields.

Subclasses implement four methods: `generate_trial`, `run_iteration`,
`error_fn`, and (optionally) `programmatic_quality`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional

from .llm import Completion


@dataclass(frozen=True)
class TrialInput:
    """One trial's deterministic starting state, derived from seed."""

    seed: int
    prompt: str
    initial_state: Any  # workload-defined: PASSAGE for w5, problem spec for w1, etc.
    metadata: dict      # any reproducibility info to carry through to results


@dataclass
class IterationOutcome:
    """One iteration's output: the revised text + bookkeeping for cost."""

    output: str           # the revised text (or whatever the workload tracks)
    completion: Completion  # raw LLM call with token counts + latency
    error: float          # workload-computed error signal


class Workload(ABC):
    """Abstract bench workload. One subclass per (task × framework × model)."""

    id: str               # e.g. "w5-adversarial-claude-haiku-4-5"
    framework: str        # e.g. "claude-agent-sdk"
    model: str            # e.g. "claude-haiku-4-5"
    loop_type: str        # e.g. "verify_revise" / "refinement" / "tool_use_retry"
    target_error: Optional[float] = None  # passed to LoopGain

    @abstractmethod
    def generate_trial(self, seed: int) -> TrialInput:
        """Deterministically produce one trial's starting input from seed.

        Pure function of seed. Same seed -> same TrialInput. No randomness
        outside the seed.
        """

    @abstractmethod
    def run_iteration(
        self,
        trial: TrialInput,
        prev_output: Optional[str],
        iteration: int,
        llm,  # bench.llm.RealAnthropic | RealOpenAI | MockLLMClient
    ) -> IterationOutcome:
        """Run one revise step. iteration is 1-indexed. prev_output is None on
        iteration 1."""

    @abstractmethod
    def error_fn(self, output: str) -> float:
        """Compute the scalar error signal from a workload output.

        Workload-specific. For code-gen: failing-test count. For RAG: 1 -
        retrieval@k. For adversarial-shortening (w5): missing-fact count. The
        only constraint is that error >= 0 and lower is better.
        """

    def programmatic_quality(self, output: str) -> Optional[float]:
        """Optional public-benchmark eval. Return a pass-rate / score in [0,1],
        or None if no programmatic eval is defined for this workload.

        Used by the bench's quality-preservation reporting (programmatic delta
        vs B20). LLM-judge runs orthogonally on all workloads regardless.
        """
        return None

    def to_metadata(self) -> dict:
        """Return cell-level metadata for the result JSONL header."""
        return {
            "id": self.id,
            "framework": self.framework,
            "model": self.model,
            "loop_type": self.loop_type,
            "target_error": self.target_error,
        }
