"""Shared task corpora and helpers for bench workloads.

Inline corpora keep the bench reproducible without external dataset downloads
at runtime. Public datasets (HumanEval, MS MARCO, τ-bench) are designed for
training/evaluation at scale; for a single-day registered bench what matters
is that the same problems are seen across conditions — the inline corpora
deliver that property and are easier to audit.

The corpora are *miniature* by design (15-25 items each), enough to seed
n=200 trials (`seed % len(corpus)`) without exhausting variety. The fact-
shuffle / parameter-perturbation per seed ensures trials differ even when
the same base problem is reused.
"""

from __future__ import annotations

import os


def in_mock_mode() -> bool:
    """True if the bench is running with BENCH_MOCK=1.

    Framework cells fall back to a direct llm.call() in mock mode so the
    harness smoke can exercise the workload's structure without the
    framework actually invoking a real API in the background.
    """
    return os.environ.get("BENCH_MOCK") == "1"
