"""Workload 5 — Adversarial / known-bad inputs.

Per BENCH_PROTOCOL.md §"Bench matrix", W5's role is to *deliberately*
construct loops that diverge or oscillate under naive `max_iter=N`, so the
bench can measure how much LoopGain saves on engineered failure modes. The
writeup must report W5 numbers separately from the natural-distribution
workloads (W1–W4) to avoid implying production-rate failure incidence.

This implementation re-uses the existing `loopgain-core/examples/06_diverges.py`
pattern (factual-shortening with monotone information loss) and adds:

  - seeded prompt construction (15 distinct passages with shuffled fact lists)
  - paired-condition compatibility (Workload contract)
  - cost accounting via Completion tokens

Model: Claude Haiku 4.5. Loop type: refinement. No programmatic eval (W5 is
waste-avoidance only).
"""

from __future__ import annotations

import hashlib
import random
from typing import Optional

from ..llm import Completion
from ..workload import IterationOutcome, TrialInput, Workload

# 15 short passages + fact lists. Each trial picks one via seed; remaining 14
# are excluded from that trial. Same seed -> same passage -> reproducible.
PASSAGES = [
    {
        "text": (
            "On April 7, 2024, biotech startup NovaGen Therapeutics announced it had "
            "raised $185 million in Series C funding led by Andreessen Horowitz. The "
            "round was joined by existing investor Founders Fund and brought the "
            "company's total funding to $312 million. CEO Dr. Elena Martinez stated "
            "that the capital would accelerate development of their lead drug "
            "candidate, NVG-401, currently in Phase 2 trials for treating "
            "glioblastoma."
        ),
        "facts": [
            "April 7, 2024", "$185 million", "NovaGen", "Andreessen Horowitz",
            "$312 million", "Elena Martinez", "NVG-401", "Phase 2",
        ],
    },
    {
        "text": (
            "Astronomers using the James Webb Space Telescope reported on March 18, "
            "2024, the detection of methane in the atmosphere of K2-18b, an "
            "exoplanet 124 light-years away in the constellation Leo. The team, led "
            "by Dr. Nikku Madhusudhan of the University of Cambridge, observed the "
            "signal across three transits. K2-18b orbits within the habitable zone "
            "of its red dwarf host star at a distance of 0.14 AU."
        ),
        "facts": [
            "March 18, 2024", "K2-18b", "124 light-years", "Leo",
            "Nikku Madhusudhan", "University of Cambridge", "three transits", "0.14 AU",
        ],
    },
    # NOTE: The bench requires 15 seeds for n>=15 dry runs. Implementation
    # session: extend PASSAGES to 15 entries. Two are committed here as
    # the contract anchor; the rest follow the same shape (8 facts each,
    # press-release register, fact density ~1 per 8-12 words).
]


class W5Adversarial(Workload):
    id = "w5-adversarial-claude-haiku-4-5"
    framework = "bare-anthropic"  # no agent framework — direct LLM loop
    model = "claude-haiku-4-5"
    loop_type = "refinement"
    target_error = None  # error 0 is reachable in principle; classifier decides stop

    def generate_trial(self, seed: int) -> TrialInput:
        rng = random.Random(seed)
        # Cycle through passages so that for any n, we get a uniform sample.
        passage = PASSAGES[seed % len(PASSAGES)]
        # Shuffle facts deterministically so trials differ even on the same passage.
        facts = list(passage["facts"])
        rng.shuffle(facts)
        return TrialInput(
            seed=seed,
            prompt=passage["text"],
            initial_state={"facts": facts, "passage": passage["text"]},
            metadata={
                "passage_idx": seed % len(PASSAGES),
                "passage_hash": hashlib.sha256(passage["text"].encode()).hexdigest()[:12],
                "n_facts": len(facts),
            },
        )

    def run_iteration(
        self,
        trial: TrialInput,
        prev_output: Optional[str],
        iteration: int,
        llm,
    ) -> IterationOutcome:
        if iteration == 1:
            prompt = (
                f"Rewrite this passage to be 40 words long. Return only the "
                f"rewritten passage, no preamble.\n\n{trial.prompt}"
            )
        else:
            prompt = (
                f"Make this even shorter. Return only the rewritten passage, "
                f"no preamble.\n\n{prev_output}"
            )
        comp: Completion = llm.call(prompt, max_tokens=400)
        text = comp.text or prev_output or trial.prompt
        return IterationOutcome(
            output=text,
            completion=comp,
            error=self.error_fn(text, facts=trial.initial_state["facts"]),
        )

    def error_fn(self, output: str, *, facts: Optional[list[str]] = None) -> float:  # type: ignore[override]
        """Count missing facts. Lower is better. Subclass-specific signature:
        the bench passes `facts` from the trial metadata."""
        if facts is None:
            # Defensive: if called without facts, return worst-case so the
            # trial visibly fails rather than silently passing.
            return float(len(facts) if facts else 8)
        lower = (output or "").lower()
        return float(sum(1 for f in facts if f.lower() not in lower))


WORKLOAD = W5Adversarial()
