# LoopGain Bench — Engineering Lessons

Forensic record of bugs surfaced while shipping the registered n=200 bench.
The bench's *value proposition* is reproducibility + honesty, so the
engineering failures that almost corrupted the data deserve to be on the
record alongside the results — including the wrong diagnoses, not just
the right ones. Sanitizing the trail to "we went straight to the fix"
would be the wrong kind of polish.

## Lesson 1 — The wrong diagnosis (LangGraph cache thread-safety)

### Symptom round 1

Registered bench v2 (concurrent runner, first attempt) wrote 200/200
W1-langgraph trials with `input_tokens == 0`, `output_tokens == 0`,
every iteration in `failed_iters`, worst-case error markers populating
`error_history`. LG terminated as "diverged" after 1–2 iters with empty
output; B20 burned through 20 iters of the same.

### Hypothesis

W1 uses LangGraph via `framework_invoke._invoke_langgraph`, which cached
the compiled `StateGraph` at module level (`_LANGGRAPH_CACHE`). With
4-thread condition concurrency inside `run_trial`, 4 threads were
invoking `graph.invoke()` on the same compiled graph simultaneously.
LangGraph compiled graphs aren't documented as thread-safe — concurrent
invokes might corrupt internal channel/checkpointer state, leading to
the observed exception storm.

### "Fix" applied

Moved the LangGraph cache to `threading.local()`, so each worker thread
gets its own compiled graph instance. Wrote a stress test: 8 concurrent
`_invoke_langgraph` calls against real Haiku, verified 0/8 zero-token
responses post-fix.

### Why we kept this change anyway

It's correct prophylactic engineering. Even if the actual v2/v3 corruption
was elsewhere (and it was — see Lesson 2), sharing a stateful framework
object across threads is fragile by default. The `threading.local`
pattern is the right design.

### What gave us false confidence

The stress test for the LangGraph cache hypothesis hit `_invoke_langgraph`
directly. It did NOT exercise `workload.run_iteration` end-to-end, which
would have included the actual culprit (signal-based timeout in worker
threads — see Lesson 2). The stress test was *correct* for the hypothesis
it tested, but the hypothesis was wrong about which code path was broken.

### Generalizable rule

**When a stress test "fixes" a bug, run the same stress test through the
full call path the production code uses, not just the suspected hot spot.**
A test that hits the suspected adapter directly proves the adapter is
fine — it doesn't prove the *bench* is fine, because the bench's call
path includes everything around the adapter too.

## Lesson 2 — The right diagnosis (`signal.SIGALRM` in worker threads)

### Symptom round 2

Registered bench v3 (with the threading.local LangGraph fix applied) ran
W1-langgraph again. Same symptom: 200/200 trials with input_tokens=0,
failed_iters populated. The Lesson-1 "fix" had not actually fixed
anything.

This time, we looked at stderr, not just the JSONLs. Stderr had been
emitting for both v2 and v3:

> `[iter 1 failed: ValueError('signal only works in main thread of the main interpreter')]`

We had missed it because the per-iteration failures only showed up in the
runner's stderr stream, not in the tee'd stdout that the run-progress
output went to. The forensic moral: read stderr when the data is wrong;
the failure message was already in front of us, we just weren't looking
in the right pipe.

### Root cause

`bench/workloads/_shared/codegen_base.py::_run_tests` enforced an exec
timeout via `signal.signal(SIGALRM, …)` + `signal.alarm(N)`. Python's
signal module raises `ValueError("signal only works in main thread of the
main interpreter")` when `signal.signal` is called from a non-main thread.

The bench runner uses condition-level concurrency: every trial's four
conditions (B5/B10/B20/LG) run in a `ThreadPoolExecutor(max_workers=4)`
inside `run_trial`. Worker threads invoking `_run_tests` raise immediately
on `signal.signal`. The exception was caught by `_run_baseline` /
`_run_loopgain`'s `except Exception` (per Methodology Lockdown #5: never
silently drop), recorded as `failed_iters[i] = i`, and the iteration's
LLM call never happened — hence `input_tokens=0`.

This is a textbook "the standard-library timeout primitive doesn't survive
the parallelism model your harness needs" failure.

### Why dry-run didn't catch this either

The dry-run executed before concurrency was added to the runner (commit
`a7754af`). Dry-run conditions ran serially in the main thread, where
`signal.SIGALRM` works fine. The condition-concurrent path was first
exercised by registered bench v2/v3.

### Fix

Replaced SIGALRM-based timeout with a daemon `threading.Thread` +
`join(timeout=N)` pattern in `_run_tests`. Daemon threads die with the
process, so an LLM that writes an infinite loop leaks a thread but
doesn't prevent process exit. Verified with a stress test: 4 concurrent
`_run_tests('while True: pass', ...)` calls all timed out at ~3.2s
(within the 3s exec budget + thread.join overhead). 8 concurrent valid
fizzbuzz tests all passed.

`concurrent.futures.ThreadPoolExecutor.submit(...).result(timeout=N)`
was tried first but doesn't work — its workers are non-daemon by default,
so the executor's `__exit__` blocks forever on a hung task. Daemon
`threading.Thread` is the right primitive here.

### Generalizable rule

**If a Python harness uses threading, no part of it can use signals.**
SIGALRM, signal.signal, signal.alarm — any of them. If you need a
timeout that survives in a worker thread, use `threading.Thread(daemon=
True)` + `join(timeout=N)` and accept the thread leak. `concurrent.
futures.ThreadPoolExecutor` looks like it should work but its
non-daemon workers will bite you.

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
