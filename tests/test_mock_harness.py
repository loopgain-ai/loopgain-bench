"""Mock-mode harness smoke test.

Verifies that the bench pipes correctly without any real API calls:
- Workload generates deterministic prompts from seed
- TrialRunner runs all four conditions (B5/B10/B20/LG)
- Cost computation against prices.json works
- JSONL output is written and parseable

These tests DO NOT validate bench results — they validate the harness. Real
results require real API calls (see BENCH_PROTOCOL.md and Makefile targets
`dry-run` and `bench`).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from bench.pricing import cost_for, load_prices, snapshot_metadata


def test_prices_load() -> None:
    prices = load_prices()
    assert "claude-haiku-4-5" in prices
    assert "gpt-4.1-mini" in prices
    assert prices["claude-haiku-4-5"].input_per_million_usd == 1.0
    assert prices["claude-haiku-4-5"].output_per_million_usd == 5.0


def test_cost_for_haiku() -> None:
    # 1k input + 1k output on Haiku 4.5: $0.001 + $0.005 = $0.006
    c = cost_for("claude-haiku-4-5", input_tokens=1000, output_tokens=1000)
    assert abs(c - 0.006) < 1e-9


def test_cost_for_unknown_model_raises() -> None:
    with pytest.raises(KeyError):
        cost_for("gemini-9000-ultra", input_tokens=1000, output_tokens=1000)


def test_snapshot_metadata_present() -> None:
    meta = snapshot_metadata()
    assert meta["date"] == "2026-05-21"
    assert "sources" in meta


def test_mock_workload_generates_deterministic_trials() -> None:
    """Same seed -> same trial. Different seeds -> different trials."""
    from bench.workloads.w5_adversarial import WORKLOAD

    t1 = WORKLOAD.generate_trial(42)
    t1_again = WORKLOAD.generate_trial(42)
    t2 = WORKLOAD.generate_trial(43)
    assert t1.prompt == t1_again.prompt
    assert t1.metadata["passage_hash"] == t1_again.metadata["passage_hash"]
    # Same passage may be reused across seeds (mod len(PASSAGES)), but fact
    # shuffle differs. At minimum metadata or fact ordering should change.
    if t1.metadata["passage_idx"] == t2.metadata["passage_idx"]:
        assert t1.initial_state["facts"] != t2.initial_state["facts"]


@pytest.mark.skipif(
    os.environ.get("BENCH_MOCK") != "1",
    reason="Run with BENCH_MOCK=1 to exercise the runner end-to-end with no API",
)
def test_runner_end_to_end_mock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Runs `run_cell` for one workload at n=2 in mock mode. Asserts JSONL
    is written and contains the four conditions per trial."""
    from bench.runner import RAW_DIR, run_cell
    from bench.workloads.w5_adversarial import WORKLOAD

    monkeypatch.setattr("bench.runner.RAW_DIR", tmp_path)
    out_path = run_cell(WORKLOAD, n=2, tag="smoke")
    assert out_path.exists()

    lines = out_path.read_text().strip().split("\n")
    header = json.loads(lines[0])
    assert header["_header"] is True
    assert header["mock_mode"] is True
    assert header["n_planned"] == 2

    trial_lines = [json.loads(l) for l in lines[1:] if not json.loads(l).get("_header")]
    # Tolerate trial-level errors (per lockdown #5); just check that we
    # produced 2 records.
    assert len(trial_lines) == 2
    for t in trial_lines:
        if t.get("_trial_error"):
            continue
        assert set(t["conditions"].keys()) == {"B5", "B10", "B20", "LG"}
        assert set(t["cost_usd"].keys()) == {"B5", "B10", "B20", "LG"}
        # Mock mode: token counts are synthetic but non-zero.
        assert t["conditions"]["B5"]["input_tokens"] > 0
