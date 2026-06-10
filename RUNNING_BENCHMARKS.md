# Running LoopGain Benchmarks — Operational Guide

Hard-won practices for running long, **paid, unattended** benchmark runs without losing
data or money. Distilled from the Bench v2 Stage-0/Stage-1 runs (BIRD, real API), where
nearly every one of these failure modes bit us at least once. Read this before kicking off
any multi-hundred-task run.

> **The one rule that matters most:** *checkpoint every trial to disk as it completes, and
> make the run resumable.* Everything else is damage reduction; this is what makes a kill
> a non-event instead of a disaster.

---

## 1. Long runs get killed — design for it, don't fight it

**Symptom.** A background run dies after ~15–20 min with a `SessionStart:resume` in the logs
and the MCP servers reconnecting. It is **not** (usually) macOS system sleep — on AC with the
display on, `powerd` prevents system sleep. The actual cause is the **Claude Code session
suspending during idle gaps** (no active turn, no user input), which kills session-bound
background processes. It resumes when you next interact.

**Mitigations, in order of importance:**

1. **Per-trial checkpointing + resume (non-negotiable).** Write each completed trial to a
   JSONL checkpoint and `flush()` + `os.fsync()` immediately. On (re)start, load completed
   `task_id`s and skip them. A kill then costs at most one in-flight trial and **never
   re-pays** for completed work. In `bench_v2` this is `--out <f>` → `<f>.partial.jsonl`;
   re-running the *same command* resumes automatically.
   - Tolerate a truncated final line (a hard kill can cut mid-write): `json.loads` per line,
     skip the one that fails.
   - **Do NOT** write results only at the end. We lost two full runs that way before adding
     checkpointing.

2. **TRUE DETACHMENT (the actual prevention — proven).** Run the worker in its own session,
   reparented to launchd, so it is **not** in the Claude session's process group and survives the
   suspend-kill outright. macOS has no `setsid` *command*, but the `os.setsid()` *syscall* works —
   `bench_v2/detach_run.py` double-forks + `os.setsid()` + redirects fds to a logfile (cwd preserved):
   ```bash
   .venv/bin/python -m bench_v2.detach_run /tmp/run.log -- \
       --source bird --bird-root "$BIRD_MINIDEV_ROOT" --provider anthropic --model claude-sonnet-4-6 \
       --n 500 --max-iter 10 --max-spend 40 --out data/results/run.json --i-understand-this-spends-money
   # the foreground call returns immediately; verify the worker is reparented + in its own session:
   ps -eo pid,ppid,sess,etime,command | grep bench_v2.detach_run | grep -v grep   # PPID should be 1
   ```
   **Verified:** a Sonnet n=500 run completed fully unattended this way, surviving multiple Claude-session
   suspends with zero manual resumes. This is the recommended launch method for any long run.

   **For the MAIN bench** (`bench.runner` → `judge` → `analyze`), use the generic local-run helpers
   `detach_pipeline.py` + `run_pipeline.sh` (repo root): `detach_pipeline.py` is the same `os.setsid()`
   double-fork but execs an arbitrary command, and `run_pipeline.sh` chains the full registered pipeline
   and exports the provider keys from `.env` (the ambient shell can carry an empty `ANTHROPIC_API_KEY`,
   and `bench.judge`/`bench.llm` don't load `.env`). Launch:
   ```bash
   .venv/bin/python detach_pipeline.py run_v0.4.0_pipeline.log -- /bin/zsh run_pipeline.sh
   ```
   The main `bench.runner` resumes per-CELL only (`--skip-existing`), not per-trial, so detachment is the
   primary defense here. Proven on the loopgain 0.4.0 re-validation: survived a multi-hour suspend mid-run.

3. **`caffeinate` (insurance, NOT the fix).** Optional wake assertion:
   ```bash
   nohup caffeinate -dimsu -t 7200 > /tmp/caffeinate.log 2>&1 &   # prevent display/idle/system sleep
   ```
   It did **not** stop the suspend-kills (it was holding when runs still died — proving the kill is the
   session suspending, not the Mac sleeping). Harmless to add; don't rely on it. `setsid` the command
   doesn't exist on macOS — use detachment (above) instead.

3. **Resume cadence.** If a run dies, just re-run the identical command; it picks up from the
   checkpoint. Keeping the session active (interacting periodically) also prevents the
   idle-suspend that triggers the kill.

---

## 2. Bound everything that can hang — three independent timeouts

A long run is a long-tailed-latency machine; anything without a timeout *will* eventually
hang forever. We hit all three of these:

1. **Per-request API timeout.** A hung HTTPS connection blocked a run for 20 min with no
   recovery. Set an explicit per-call timeout (e.g. `timeout=60`) on **every** provider call
   and wrap it in exponential-backoff retry (e.g. 4 tries, 2/4/8 s). A stuck call then fails
   fast → retries → or surfaces as a per-task error, never blocks.

2. **Per-execution timeout on model-generated code.** A model emitted a pathological SQL
   (cartesian join over a 184k-row table ≈ 6×10¹⁵ rows) and SQLite ran it forever, pegging a
   core at 99% CPU. `busy_timeout` only covers *locks*, not execution. Use a watchdog:
   ```python
   wd = threading.Timer(5.0, con.interrupt); wd.start()   # sqlite3.Connection.interrupt() is thread-safe
   try: rows = con.execute(sql).fetchall()
   finally: wd.cancel()
   ```
   Generalizes to any model-generated code you execute: hard wall-clock bound it.

3. **Per-task isolation.** Wrap each task's loop in `try/except` so one bad task records an
   error trial and the run continues, instead of crashing the whole batch.

---

## 3. Spend safety — make a paid run impossible to start by accident

- **Default to mock/dry.** Provider defaults to a `mock` ($0, no network) that returns
  scripted outputs; a real provider is opt-in.
- **Explicit consent flag.** A real provider without `--i-understand-this-spends-money` is
  refused outright.
- **Hard `--max-spend` cap**, checked *after every task*, aborting before the next call.
  Sum cost across resume from per-trial tokens (not just the current process), or the cap
  resets to zero on every resume.
- **Dry-run stage-gate.** Before the confirmatory run, do `--n 10` (~$1–2) to (a) confirm the
  oracle works on real data, (b) check the difficulty band, (c) sanity-check cost. Extrapolate
  the per-task cost from it before committing to the full run.
- **Smoke-test a new model id at `--n 1`** before a long run — an invalid model string would
  otherwise burn the whole run on failed calls.

---

## 4. Validate the pipeline at $0 before paying

- **Mock mode + self-test.** A scripted `mock` provider drives the full loop/oracle/metrics
  path with deterministic trajectories (a planted found-it-then-broke-it, an oscillation, a
  clean converge, a never-correct), asserting every metric. Run `python -m bench_v2.selftest`
  (zero spend) and require it green before any paid run. It also exercises the spend-cap abort
  and the checkpoint/resume path.
- **$0 gold sanity check on real data.** After downloading data, run the *reference* answers
  through the oracle (no model calls) to confirm the oracle + data wiring work before paying
  for model calls.

---

## 5. Data: provenance, integrity, sampling

- **Trusted host + integrity check.** Download from a trusted CDN (we used Google over a
  China-hosted mirror), verify **SHA-256** and the zip CRC, and pin the hash in the results
  doc. Build databases locally from inspectable text where possible; when only a prebuilt
  binary exists, sandbox it (see §6).
- **Deterministic, representative sampling.** If the dataset is ordered by group (BIRD is
  ordered by database), taking the first-N is **group-skewed**. Shuffle with a fixed seed,
  then take N, so the subset spans all groups/difficulties. Use the *same* seed across model
  tiers so they run the identical task set (paired comparison).
- **Reuse paid work when scaling N.** To go from n=150 → n=500, pre-seed the n=500 checkpoint
  with the existing 150 trial records (same dict format), then run n=500 — it skips the 150
  and only bills the new 350.

---

## 6. Execute model-generated code read-only / sandboxed

Model output run against your data is an attack/footgun surface. For SQL we:
- open every DB **read-only + immutable** (`file:...?mode=ro&immutable=1`),
- allow only a **single `SELECT`/`WITH`** statement (reject multi-statement / DDL / DML),
- disable extension loading,
- time-bound execution (§2.2).

So a generated `DROP`/`DELETE`/`ATTACH`/runaway query can't write, escalate, or hang.

---

## 7. Monitoring a background run (read the signals correctly)

- **Liveness ≠ pgrep.** `pgrep -f bench_v2.runner` also matches the shell wrapper's
  command-line text and your own `grep` — it gives false "alive" readings. Judge liveness by
  **checkpoint write recency** (`stat -f %m`) and CPU state instead.
- **Diagnose a stall by CPU:** `ps -o pid,%cpu,etime`:
  - **~99% CPU, no new writes** → stuck in local compute (runaway query / infinite loop) — needs an execution timeout (§2.2).
  - **~0% CPU, no new writes** → blocked on network I/O (an API call) — needs a request timeout (§2.1), or it's just mid-trial.
- **Progress = checkpoint line count vs total.** `wc -l <out>.partial.jsonl`.
- **Throughput / ETA:** trials-per-minute from two checkpoint reads × tasks-remaining. Budget
  ~10 sequential API calls per trial (no stop-at-success in the measurement harness).

---

## 8. Quick reference — a clean run from cold

```bash
# 0. validate at $0
.venv/bin/python -m bench_v2.selftest

# 1. data (manual, trusted host, verified) — see bench_v2/README.md §Data
export BIRD_MINIDEV_ROOT=.../MINIDEV

# 2. keep the machine awake
nohup caffeinate -dimsu -t 5400 > /tmp/caffeinate.log 2>&1 &

# 3. dry-run gate (~$1-2)
.venv/bin/python -m bench_v2.runner --source bird --bird-root "$BIRD_MINIDEV_ROOT" \
  --provider openai --model gpt-4.1-mini --n 10 --max-iter 10 --max-spend 5 \
  --out data/results/drygate.json --i-understand-this-spends-money

# 4. confirmatory run (checkpointed; re-run identical to resume after any kill)
.venv/bin/python -m bench_v2.runner --source bird --bird-root "$BIRD_MINIDEV_ROOT" \
  --provider openai --model gpt-4.1-mini --n 500 --max-iter 10 --max-spend 6 \
  --out data/results/run.json --i-understand-this-spends-money

# monitor (NOT pgrep): progress + recency
wc -l data/results/run.json.partial.jsonl ; stat -f "%Sm" data/results/run.json.partial.jsonl
```

**If it dies:** re-run step 4 verbatim. It resumes from the checkpoint. Don't panic, don't restart from zero.

---

## 9. Refreshing the public dashboard after a re-run

`dashboard.loopgain.ai/benchmark` is **data-driven** from the receiver's public bench tenant
(`cust_7931de9f766452ac`) — the Spotlight is `SUM(actual_dollars_saved)` over that tenant's
`loop_events`. After a bench re-run you refresh it by re-uploading; there is no dashboard code or
copy to edit.

Ingestion is **append-only**, so use the idempotent `--reset` (clear-then-load) — a naive re-run
*doubles* the totals.

```bash
cd loopgain-bench
# 1. dry-run FIRST (no writes) — confirm count + $ match RESULTS.md:
.venv/bin/python bench/upload_to_dashboard.py --dry-run
#    -> Trials 2000 / Sum saved $25.11 / spent $1.94 / outcomes match
# 2. live reset + reupload — BACKGROUND it (~6-13 min, rate-limited ~3/s):
nohup .venv/bin/python bench/upload_to_dashboard.py --reset > /tmp/lg_reupload.log 2>&1 &
# 3. verify (unauthenticated):
curl -s "https://telemetry.loopgain.ai/v1/public/benchmark/stats?cb=$RANDOM" \
  | python3 -c "import sys,json;t=json.load(sys.stdin)['totals'];print(t['event_count'],t['total_actual_dollars_saved'])"
#    -> 2000  25.11
```

`--reset` POSTs `{"confirm":"reset"}` to the receiver's self-scoped `POST /v1/aggregate/reset`
(deletes only the bench tenant's own rows). `--library-version` (default `0.4.0`) stamps the payloads.

**Gotcha — reset read-timeout:** deleting ~2,000 rows on D1 can exceed the client read timeout
while the DELETE *still completes server-side*, leaving the tenant empty and the upload aborted
(`reset FAILED: status=-1 TimeoutError`). The timeout is now 120s, but if it still aborts: the
tenant is already empty, so just re-run the uploader **without `--reset`** (append-to-empty =
exactly 2000). Always check `event_count` after.
