# Bench v2 — Stage 0: Oscillation Base Rate (BIRD-only)

Measures how often a realistic, **verifier-gated** verify-revise loop reaches a
correct state and then **degrades it** by iterating further
("found-it-then-broke-it"). Frozen design + decision thresholds:
[`../BENCH_V2_OSCILLATION_BASERATE_PREREG_DRAFT.md`](../BENCH_V2_OSCILLATION_BASERATE_PREREG_DRAFT.md).

**Status:** harness built and mock-validated (zero spend). Real runs are **gated**
behind an explicit flag and a hard spend cap. Nothing has been run against a paid
API yet.

## Design in one paragraph
A single fixed verify-revise driver (no framework confound) runs each task to a
fixed `max_iter=10` **without** a target short-circuit — so a degrade *after* a
correct answer can actually be observed (mirrors the v1 `B20` condition). The
error signal is BIRD execution accuracy (`s=0` correct, `s=1` wrong), compared
**order-insensitively** so an `ORDER BY` difference is not a false degrade. Every
found-it-then-broke-it event is **re-confirmed** by re-executing the best vs.
terminal query (guards against flaky execution). Two cross-vendor model tiers
(gpt-4.1-mini + Claude Sonnet) are run as separate strata.

## Layout
| File | Role |
|---|---|
| `data.py` | Task loading: `mock_tasks()` (fixture) + `load_bird_minidev()` (offline read) |
| `llm.py` | Providers: `mock` ($0, scripted), `openai`, `anthropic` (lazy SDKs); usage/cost tracking |
| `oracle.py` | SQL execution, order-insensitive result matching, feedback, strong-oracle re-confirm |
| `loop.py` | Fixed verify-revise driver (no target short-circuit); per-iteration trajectory |
| `metrics.py` | Frozen metrics + verdict (found-it-then-broke-it ≥15% / 5–15% / <5%) |
| `runner.py` | Orchestration, JSONL logging, **spend cap**, paid-run refusal guard |
| `selftest.py` | Zero-spend end-to-end validation (26 checks) |

## Run it

### 1. Validate the harness (zero spend, no network) — do this anytime
```bash
.venv/bin/python -m bench_v2.selftest          # 26 checks, exits 0 when wired correctly
.venv/bin/python -m bench_v2.runner --source mock --provider mock   # bare pipeline smoke
```

### 2. Data (manual, free) — required before any real run
BIRD Mini-Dev is **not** fetched by the harness (offline-by-default). Download the
500-example Mini-Dev set + its SQLite databases from the official BIRD release,
unpack to a directory, and point the runner at it:
```
<BIRD_ROOT>/
  mini_dev_sqlite.json            # [{db_id, question, SQL, difficulty}, ...]
  dev_databases/<db_id>/<db_id>.sqlite
```
```bash
export BIRD_MINIDEV_ROOT=/path/to/bird_minidev
```

### 3. Dry-run stage-gate (~$1–2, GATED) — verify oracle + difficulty band at n=10
```bash
.venv/bin/python -m bench_v2.runner --source bird --provider openai --model gpt-4.1-mini \
    --n 10 --max-iter 10 --max-spend 5 --out data/results/v2_drygate_openai.json \
    --i-understand-this-spends-money
```

### 4. Confirmatory run (~$18 total, GATED) — n=150 per tier
```bash
# mid tier (OpenAI)
.venv/bin/python -m bench_v2.runner --source bird --provider openai --model gpt-4.1-mini \
    --n 150 --max-iter 10 --max-spend 80 --out data/results/v2_bird_gpt41mini.json \
    --i-understand-this-spends-money
# frontier tier (Anthropic)
.venv/bin/python -m bench_v2.runner --source bird --provider anthropic --model claude-sonnet-4-6 \
    --n 150 --max-iter 10 --max-spend 80 --out data/results/v2_bird_sonnet.json \
    --i-understand-this-spends-money
```

## Spend safety (three independent guards)
1. Provider defaults to `mock` ($0). A real provider is opt-in.
2. A real provider **without** `--i-understand-this-spends-money` is refused outright.
3. `--max-spend` is a hard cap, checked after every task; the run aborts before the
   next call once the running cost estimate would exceed it. Prices are frozen in
   `llm.py::PRICES` (match the v1 `prices.json` snapshot).

## Reading the output
Per tier the runner prints `ftb_rate` (found-it-then-broke-it among tasks that ever
reached a correct state) and the frozen **verdict**: `HEADLINE` (≥15%),
`WORKLOAD-DEPENDENT` (5–15%), or `NICHE` (<5%). **Report each tier separately** —
never pool the two models into one number (capability swings the rate ~10× per the
research). The full per-iteration trajectories are in the JSON for downstream analysis
(e.g. feeding the real LoopGain monitor in a later Stage-3 head-to-head).
```
