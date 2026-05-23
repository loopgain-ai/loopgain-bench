# LoopGain Bench — Engineering Lessons

Forensic record of bugs surfaced while shipping the registered n=200 bench.
The bench's *value proposition* is reproducibility + honesty, so the
engineering failures that almost corrupted the data deserve to be on the
record alongside the results.

## Lesson 1 — `signal.SIGALRM` is not thread-safe (and the bench is)

### Symptom

During registered-bench v2 and v3, every W1 (code-gen) trial wrote with
`input_tokens == 0` and `output_tokens == 0`, and every iteration recorded
in `failed_iters`. Worst-case error markers (`1e6`) populated
`error_history`. LG terminated as "diverged" after 1–2 iters with garbage
output; B20 terminated at iter 20 with the same garbage.

### Root cause

`bench/workloads/_shared/codegen_base.py::_run_tests` enforced an exec
timeout via `signal.signal(SIGALRM, …)` + `signal.alarm(N)`. Python's
signal module raises `ValueError("signal only works in main thread of the
main interpreter")` when `signal.signal` is called from a non-main thread.

The bench runner uses condition-level concurrency: every trial's four
conditions (B5/B10/B20/LG) run in a `ThreadPoolExecutor(max_workers=4)`
inside `run_trial`. Worker threads invoking `_run_tests` raise immediately
on `signal.signal`. The exception is caught by `_run_baseline` /
`_run_loopgain`'s `except Exception` (per Methodology Lockdown #5: never
silently drop), recorded as `failed_iters[i] = i`, and the iteration's
LLM call never happens — hence `input_tokens=0`.

This is a textbook "the standard-library timeout primitive doesn't survive
the parallelism model your harness needs" failure.

### Why dry-run didn't catch it

The dry-run executed before concurrency was added to the runner (commit
`a7754af`). Dry-run conditions ran serially in the main thread, where
`signal.SIGALRM` works fine. The condition-concurrent path was first
exercised by the registered bench itself — a textbook "the test
environment didn't exercise the production path" failure mode.

### Fix

Replaced SIGALRM-based timeout with thread-safe
`concurrent.futures.ThreadPoolExecutor.submit(...).result(timeout=N)` in
`_run_tests`. Each exec or eval call spawns a transient 1-worker pool;
on timeout, the future is abandoned (the worker thread leaks for the
lifetime of the bench process — acceptable for a single-shot bench run,
since alternative subprocess-based isolation would add ~100ms per call
× 16K calls ≈ 27min of overhead).

### Generalizable rule

**If a Python harness uses threading, no part of it can use signals.**
SIGALRM, signal.signal, signal.alarm — any of them. Same applies to
`os.setsigprocmask` and friends. If you need a timeout in a threaded
harness, use `concurrent.futures` or `asyncio` exclusively.

## Lesson 2 — Module-shared LangGraph compiled graph (false alarm, but)

### What happened

Before diagnosing Lesson 1, we hypothesized the W1-langgraph corruption
was caused by a module-level cached LangGraph compiled graph being shared
across the 4 condition threads. We "fixed" it by moving the graph cache
to a `threading.local()`. The fix is structurally correct (LangGraph
compiled graphs are not documented as thread-safe; sharing one across
concurrent invocations could in principle corrupt internal state). But
the actual cause of the v2/v3 corruption was Lesson 1, not the cache.

### Why we kept the threading.local change

It's correct prophylactic engineering. Even if the v2/v3 trials would
have worked with the module-shared graph, a future change could mutate
the graph's internal state in a way that breaks under concurrency. The
`threading.local` cache compiles once per worker thread and reuses across
iterations — zero contention, near-zero overhead.

### Generalizable rule

**Adapters that cache stateful framework objects (compiled graphs,
session handles, etc.) must use `threading.local()` if the harness is
multi-threaded.** A stateless adapter (e.g. `_invoke_langchain` creating
a fresh `RunnableLambda` per call) needs no special handling.

## Lesson 3 — Per-cell tripwire after first N trials

### Why

Both bugs above silently wrote 200 broken JSONL records before being
caught by manual inspection. By construction, the bench harness writes
*something* per trial — there's no built-in failure mode for "trial
completed but every iteration failed." The corruption looks like a
successful trial to the file writer.

### Mitigation

`run_cell` now monitors the first 5 trials' token counts. If all five
have `input_tokens == 0` across both LG and B20, the cell aborts with a
loud stderr message naming the workload, tag, and likely cause:
> "first 5 trials all have 0 input/output tokens. This indicates a
> harness-level bug (e.g. thread-safety in the adapter, broken
> framework_invoke, or per-iteration exception swallowing all LLM
> calls). Stopping cell."

Cost of the tripwire: ~5 trials × ~$0.01 each = ~$0.05 wasted per
broken cell. Versus letting a corrupted cell run to n=200 and burn
~$5 — easily 100× ROI.

### Generalizable rule

**Bench harnesses need early sanity checks on aggregate signals that
should never plausibly be zero.** Token counts, error magnitudes, latency
— anything where "zero across the board" is a smoking gun for a
harness bug, not a workload result.

## Lesson 4 — Concurrency math: count inflight calls, not threads

### What we tried first

`make bench` initially used `--trials-parallel 8 --cells-parallel 2` on
top of always-on condition-level concurrency. The math: 2 cells × 8
trials × 4 conditions = **64 inflight LLM calls** at peak.

### What happened

After ~1h, two cells had a header written and 0 trials landed. Process
got SIGKILL'd (likely OOM or HTTP connection-pool exhaustion). 64
inflight calls is over httpx's default pool size and probably triggered
queueing-induced deadlock or memory pressure.

### Fix

Dropped to `--cells-parallel 2 --trials-parallel 1` (default). Math:
2 cells × 1 trial × 4 conditions = **8 inflight LLM calls**. Well within
both provider rate limits and HTTP library defaults. Wall-clock
projection: 58h serial → ~7-14h with this concurrency level.

### Generalizable rule

**Count inflight calls end-to-end, not just thread count.** Nested
concurrency multiplies. The right peak number depends on the slowest
shared resource (HTTP connection pool, provider rate limit, memory) —
not on how many CPU cores are available.

## What stayed clean

- All 5 non-langgraph framework adapters (langchain, crewai, autogen,
  openai-agents, claude-agent-sdk) passed the 8-concurrent stress test
  with zero zero-token responses. Each is stateless or uses fresh
  per-call instances — no module-level mutable state to share.
- Methodology lockdowns held throughout. Each broken run was discarded
  per Lockdown #9 ("raw data is immutable" applies to *real data*, not
  to harness-failure artifacts).
- No protocol amendments were needed for the engineering fixes —
  Methodology Lockdown #4 (same seeds across conditions) and #7 (same
  wall-clock environment) survive concurrent execution; the lockdowns
  describe scientific identity, not execution strategy.
