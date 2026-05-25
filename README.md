# loopgain-bench

Reproducible benchmark for **[LoopGain](https://loopgain.ai)** — measures cost, iterations, wall-clock, and output quality on real agentic loops, baseline-vs-LoopGain, across the major Python agent frameworks.

> **Status: REGISTERED RESULTS LANDED (2026-05-25).** Across 2,000 real-API trials over 10 cells, LoopGain reduced median API spend by **93.5% vs `max_iter=20`**, reduced median wall-clock by **~10×**, preserved output quality on natural-distribution workloads, and *improved* output quality on engineered-failure workloads. **Zero of six kill criteria fired.** Full writeup: [`RESULTS.md`](./RESULTS.md). Pre-registration: [`BENCH_PROTOCOL.md`](./BENCH_PROTOCOL.md) (locked 2026-05-21, before any cell beyond the n=10 dry-run captured real data).

## Headline results

Across the full registered run (10 cells × n=200 paired trials = 8,000 loop runs + 1,800 pairwise judge comparisons):

| | B5 | B10 | **B20** | **LoopGain** |
|---|---:|---:|---:|---:|
| Total API spend | $7.00 | $13.97 | **$27.61** | **$1.80** |
| Median wall-clock per trial | 26.2s | 49.1s | **93.0s** | **9.8s** |
| Implied savings vs B20 | — | — | — | **93.5% cost / 89.5% time** |

**Quality** — three-layer story (full numbers in [`RESULTS.md` §Three findings](./RESULTS.md#three-product-axes-four-findings-to-surface-honestly)):
- **Preserved** on natural-distribution workloads (W1–W2 judge winrate 0.55–0.62 with CI excluding null on most cells; W3 cells 0.497–0.517 tie-dominated, preservation-by-construction since both LG and B20 produce the same correct tool call ~90% of the time).
- **Improved** on engineered-failure workloads (W5 winrate 0.88–0.93 across three adapters; best-so-far rollback returns the iter that worked rather than the iter that degraded).
- **Aggregate** weighted-average pairwise preference for LG vs B20 across 1,800 judge comparisons: **0.681** — over two-thirds.

**Kill criteria** — none fired. Two pre-registered floors were missed without firing kill: H-EARLYWARN median lead (2 iters observed vs ≥ 3 predicted; kill at < 1) and H-FRAMEWORK-PARITY W1 spread (5.8 pp observed vs ≤ 5 pp predicted; kill at > 15 pp). Both disclosed honestly in [`RESULTS.md` §Limitations](./RESULTS.md#limitations-to-disclose).

**One trial in particular illustrates the mechanism**: on a code-gen problem (MBPP/138, Haiku 4.5 + LangGraph), `max_iter=20` *found the correct answer at iteration 8*, kept iterating, and degraded back to broken code (failing 11/11 tests) by iteration 20. LoopGain detected TARGET_MET at iteration 2 and stopped with the working code. See [`RESULTS.md` §Hero story](./RESULTS.md#hero-story) for the chart and the discussion of why the mechanical hero-pick (a different trial) is disclosed separately from this DM-screenshot illustration.

## What this measures

LoopGain replaces the universal `max_iterations=N` cap in outer-driven iterative LLM loops — any loop where each iteration produces an observable error signal — with a real-time loop-gain (Aβ) monitor that detects FAST_CONVERGE / CONVERGING / STALLING / OSCILLATING / DIVERGING and rolls back to best-so-far on divergence. The bench tests five loop patterns: verify-revise (W1 code-gen), critique-revise debate (W2), planner-executor with tool use (W3), iterative RAG (W4), and refinement (W5 adversarial shortening).

This benchmark answers: *what does that actually save, on real loops, across frameworks?*

| Metric | What we measure |
|---|---|
| **Cost** | $ per task, computed against frozen provider rates ([`prices.json`](./prices.json)) |
| **Iterations** | Median + IQR iterations per task |
| **Wall-clock** | End-to-end latency including network |
| **Quality** | LLM-judge pairwise winrate (cross-vendor) + programmatic eval delta where available |
| **Early-warning lead time** | On loops that catastrophically diverge under `max_iter=20`, how many iterations before catastrophe LoopGain flags |
| **False-stop rate** | The kill metric: % of LoopGain stops where extending to `max_iter=20` would have produced strictly better output (AND-rule on cells with programmatic eval; judge-only on W5 per Amendment 2026-05-21b) |

Pre-registered predictions and kill criteria: see [`BENCH_PROTOCOL.md`](./BENCH_PROTOCOL.md). Observed outcomes vs predictions: see [`RESULTS.md`](./RESULTS.md).

## Methodology

Four conditions, **paired within each trial** (same prompt, same model, same seed):

- `B5` — `max_iter=5` (LangChain / CrewAI common default)
- `B10` — `max_iter=10` (LangGraph default)
- `B20` — `max_iter=20` (production-cautious; ground-truth oracle)
- `LG` — LoopGain v0.2.0, default thresholds

10 measurement cells (workload × framework). `n = 200` paired trials per cell. **8,000 total loop runs + 1,800 pairwise judge comparisons.**

All ten [methodology-integrity lockdowns](./BENCH_PROTOCOL.md#methodology-integrity-locked-in-safeguards) held under inspection of the registered run: judge model ≠ loop model, position-randomized pairwise, no mid-run filtering, no optional stopping, immutable raw data — see [`RESULTS.md` §Methodology integrity](./RESULTS.md#methodology-integrity) for the audit. Seven amendments landed during the run; all scenario-design class with predicted floors and kill criteria unchanged.

## Run it yourself

```bash
git clone https://github.com/loopgain-ai/loopgain-bench
cd loopgain-bench
make install-dev
```

```bash
# 1. Mock-mode smoke test — no API calls, deterministic, fast.
make mock

# 2. Real-API dry-run, n=10 per cell. Catches adapter bugs cheap. ~$2.
export ANTHROPIC_API_KEY=...
export OPENAI_API_KEY=...
make dry-run

# 3. Full registered run, n=200 per cell. ~$50 spend, ~4-8h wall-clock
#    on a single Mac with cells-parallel=2 (Anthropic + OpenAI provider
#    buckets overlapped). --skip-existing makes the run resumable.
make bench

# 4. Cross-vendor pairwise judge on the registered JSONLs. ~$1-2.
#    RAG cells skipped — they use programmatic eval (retrieval@k).
make judge

# 5. Analysis: six tables + six charts + hero-story selector.
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
├── README.md               # this file
├── RESULTS.md              # the writeup — 2,000-trial registered results
├── BENCH_PROTOCOL.md       # pre-registration (LOCKED 2026-05-21) + 7 amendments
├── LESSONS.md              # engineering forensics: bugs caught + fixes applied
├── KICKOFF.md              # original kickoff brief (historical record)
├── prices.json             # provider pricing snapshot (LOCKED 2026-05-21)
├── Makefile                # mock / dry-run / bench / judge / analyze targets
├── pyproject.toml
├── bench/
│   ├── workload.py         # Workload base class — implement once per task
│   ├── runner.py           # paired B5/B10/B20/LG runner, condition+cell concurrent
│   ├── pricing.py          # cost computation against prices.json
│   ├── judge.py            # cross-vendor pairwise judge + per-cell runner
│   ├── llm.py              # real Anthropic / OpenAI clients + mock client
│   └── workloads/
│       ├── _shared/        # task corpora + framework_invoke + base classes
│       ├── w1_codegen_{langgraph,claude_agent_sdk}.py    # MBPP+ codegen
│       ├── w2_debate_{autogen,crewai}.py                  # rubric-graded debate
│       ├── w3_planner_{langgraph,openai_agents}.py        # BFCL v4 tool use
│       ├── w4_rag_langchain.py                            # BEIR/SciFact retrieval
│       └── w5_adversarial{,_langgraph,_crewai}.py         # engineered failure
├── analysis/
│   ├── run.py              # six analysis tables + hero-story selector
│   └── charts.py           # six RESULTS.md PNGs (matplotlib)
├── data/
│   ├── raw/                # immutable registered JSONLs (10 cells + 9 judge)
│   └── results/            # analysis outputs (json + csv + charts/*.png)
└── tests/
    ├── test_mock_harness.py
    └── test_adapter_parity.py
```

## What's open-source vs. what's the upsell

This repo is Apache-2.0 — clone it, run it, modify it, run it on your own loops. The `loopgain` library it benchmarks is also Apache-2.0 ([github.com/loopgain-ai/loopgain](https://github.com/loopgain-ai/loopgain)).

The **upsell** is the hosted SaaS dashboard at [dashboard.loopgain.ai](https://dashboard.loopgain.ai) and the managed telemetry receiver at `telemetry.loopgain.ai` — fleet-wide observability, alerts, history. Self-host the [receiver](https://github.com/loopgain-ai/telemetry-receiver) if you want to keep everything local.

## License

Apache-2.0. See [LICENSE](./LICENSE).
