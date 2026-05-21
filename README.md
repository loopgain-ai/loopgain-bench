# loopgain-bench

Reproducible benchmark for **[LoopGain](https://loopgain.ai)** — measures cost, iterations, wall-clock, and output quality on real agentic loops, baseline-vs-LoopGain, across the major Python agent frameworks.

> **Status: pre-data.** Pre-registration is locked in [`BENCH_PROTOCOL.md`](./BENCH_PROTOCOL.md) (2026-05-21). The full N≥200 run has not yet been executed. This README is updated when results land.

## What this measures

LoopGain replaces the universal `max_iterations=N` cap in agentic verify-revise loops with a real-time loop-gain (Aβ) monitor that detects FAST_CONVERGE / CONVERGING / STALLING / OSCILLATING / DIVERGING and rolls back to best-so-far on divergence.

This benchmark answers: *what does that actually save, on real loops, across frameworks?*

| Metric | What we're measuring |
|---|---|
| **Cost** | $ per task, computed against frozen provider rates ([prices.json](./prices.json)) |
| **Iterations** | Median + p95 iterations per task |
| **Wall-clock** | End-to-end latency including network |
| **Quality (preservation)** | LLM-judge pairwise winrate (LoopGain vs `max_iter=20`) + programmatic eval delta where available |
| **Early-warning lead time** | On loops that catastrophically diverge under `max_iter=20`, how many iterations before catastrophe LoopGain flags |
| **False-stop rate** | The kill metric: % of LoopGain stops where extending to `max_iter=20` would have produced strictly better output |

Pre-registered predictions and kill criteria: see [`BENCH_PROTOCOL.md`](./BENCH_PROTOCOL.md).

## Methodology

Four conditions, **paired within each trial** (same prompt, same model, same seed):

- `B5` — `max_iter=5` (LangChain / CrewAI common default)
- `B10` — `max_iter=10` (LangGraph default)
- `B20` — `max_iter=20` (production-cautious; ground-truth oracle)
- `LG` — LoopGain v0.2.0, default thresholds

10 measurement cells (workload × framework). `n ≥ 200` paired trials per cell. 8,000 total loop runs.

All five [methodology-integrity safeguards](./BENCH_PROTOCOL.md#methodology-integrity-locked-in-safeguards) are committed-in-protocol: judge model ≠ loop model, position-randomized pairwise, no mid-run filtering, no optional stopping, immutable raw data.

## Run it yourself

```bash
git clone https://github.com/loopgain-ai/loopgain-bench
cd loopgain-bench
pip install -e ".[dev,all]"

# 1. Mock-mode smoke test — no API calls, deterministic, fast.
make mock

# 2. Real-API dry-run, n=10 per cell. Catches adapter bugs. ~$5.
export ANTHROPIC_API_KEY=...
export OPENAI_API_KEY=...
make dry-run

# 3. Full registered run, n=200 per cell. ~$30-60. Hands-off.
make bench

# 4. Analysis. Idempotent on raw data.
make analyze
```

If you want runs to land in the public LoopGain dashboard tenant for comparison, set:

```bash
export LOOPGAIN_TELEMETRY_TOKEN=<bench-tenant-token>
export LOOPGAIN_TELEMETRY_ENDPOINT=https://telemetry.loopgain.ai/v1/aggregate
```

## Repo layout

```
loopgain-bench/
├── BENCH_PROTOCOL.md       # pre-registration (LOCKED 2026-05-21)
├── prices.json             # provider pricing snapshot (LOCKED 2026-05-21)
├── README.md               # this file
├── Makefile                # mock / dry-run / bench / analyze targets
├── pyproject.toml
├── bench/
│   ├── workload.py         # Workload base class — implement once per task
│   ├── runner.py           # TrialRunner — paired B5/B10/B20/LG with same seed
│   ├── pricing.py          # cost computation against prices.json
│   ├── judge.py            # LLM-judge pairwise (cross-model, position-randomized)
│   ├── llm.py              # real Anthropic / OpenAI clients + mock client
│   └── workloads/
│       ├── w5_adversarial.py   # Workload 5: engineered failure inputs (proof-of-concept)
│       └── (w1-w4 to be implemented)
├── analysis/
│   └── run.py              # produces aggregates / quality CIs / segmentation
├── data/
│   └── raw/                # immutable raw trial outputs (one JSONL per cell)
└── tests/
    └── test_mock_harness.py
```

## What's open-source vs. what's the upsell

This repo is Apache-2.0 — clone it, run it, modify it, run it on your own loops. The `loopgain` library it benchmarks is also Apache-2.0 ([github.com/loopgain-ai/loopgain](https://github.com/loopgain-ai/loopgain)).

The **upsell** is the hosted SaaS dashboard at [dashboard.loopgain.ai](https://dashboard.loopgain.ai) and the managed telemetry receiver at `telemetry.loopgain.ai` — fleet-wide observability, alerts, history. Self-host the [receiver](https://github.com/loopgain-ai/telemetry-receiver) if you want to keep everything local.

## License

Apache-2.0. See [LICENSE](./LICENSE).
