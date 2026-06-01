"""LoopGain Bench v2 — oscillation base-rate harness (Stage 0, BIRD-only).

Measures how often a realistic, verifier-gated verify-revise loop reaches a
good (correct) state and then DEGRADES it by iterating further
("found-it-then-broke-it"). See ``../BENCH_V2_OSCILLATION_BASERATE_PREREG_DRAFT.md``
for the frozen design and decision thresholds.

Default-safe: the runner uses the ``mock`` provider unless a real provider is
explicitly selected, and enforces a hard spend cap. No network or API calls
happen in mock mode.
"""

__all__ = ["data", "llm", "oracle", "loop", "metrics", "runner"]
