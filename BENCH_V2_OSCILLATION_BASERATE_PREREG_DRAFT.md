# Pre-Registration (DRAFT) — Oscillation Base Rate on Realistic-Difficulty Verifier-Gated Loops

**Status: DRAFT — scope narrowed to Stage 0, decisions locked 2026-05-31; NOT YET RUN.** This involves
**new API spend** (unlike the zero-spend re-analysis in `STABILITY_VS_CONFIDENCE_*`).

**Locked decisions (2026-05-31):**
- **Budget: $100 total ceiling.** This funds **Stage 0 (BIRD-only)** richly. It does **NOT** fund
  SWE-bench Verified — both on API cost (~$1–10/instance × 100 × 2 tiers ≫ $100) and on infra (Dockerized
  per-repo test harness). **SWE-bench is deferred to its own budget + harness build, gated on Stage 0.**
- **§4 decision thresholds FROZEN**: found-it-then-broke-it ≥15% HEADLINE / 5–15% workload-dependent /
  <5% NICHE. No further movement.
- **Scope = Stage 0 first** (forced by budget; also correct sequencing — de-risk on the cheap clean oracle).
- **Model tiers = cross-vendor, FINAL** (for external validity of a generalizing base rate):
  mid = **gpt-4.1-mini**, frontier = **Claude Sonnet 4.x**, BIRD Mini-Dev **n=150**, hard live spend
  cap **$80** (≈$18 expected). Cross-vendor means any A-vs-B difference is capability+vendor confounded
  — report as "model A vs B," not a capability slope. (Opus reserved for the later SWE-bench round.)

Remaining before this flips to REGISTERED: (a) build the fixed verify-revise harness (zero-spend code +
mock-mode validation), (b) dry-run stage-gate at n=10/cell to verify oracle + difficulty band (~$1–2),
then (c) the confirmatory n=150 run (~$18) on explicit go.

**Informed by**: deep-research report "AI Agent Loops in Production and the Base Rate of
Non-Monotonic Behavior" (2026-05-31) and the re-analysis verdict in
[`STABILITY_VS_CONFIDENCE_RESULTS.md`](./STABILITY_VS_CONFIDENCE_RESULTS.md).

**Informed by**: deep-research report "AI Agent Loops in Production and the Base Rate of
Non-Monotonic Behavior" (2026-05-31) and the re-analysis verdict in
[`STABILITY_VS_CONFIDENCE_RESULTS.md`](./STABILITY_VS_CONFIDENCE_RESULTS.md).

---

## 0. The one question this answers

The existing bench measured LoopGain on a **bimodal** workload set: easy tasks calibrated to one-shot
~80–90%, plus an **adversarial** set engineered to fail (W5). There is nothing in the realistic middle.
The re-analysis (`STABILITY_VS_CONFIDENCE_RESULTS.md`) showed LoopGain's differentiated value — catching
loops that *intermittently improve* so a confidence/plateau rule never trips — is **real but
concentrated in W5**. We do not know the **base rate** in realistic loops.

> **How often, on realistic-difficulty, verifier-gated verify-revise loops, does a loop reach a good
> (passing/correct) state and then a later iteration DEGRADE it ("found-it-then-broke-it")?**

The answer decides positioning (pre-registered §4): headline feature vs. targeted bonus vs. niche.

## 1. The strategic tension this experiment must respect (read before designing)

The research's load-bearing finding (Kamoi et al., TACL 2024): the **external-oracle distinction** is the
single biggest moderator of whether iteration helps or hurts.

- **No reliable oracle** (intrinsic self-correction): degradation is **common** (correct→incorrect
  overturn 7.9%–58.8% across models; GPT-3.5 changes its answer >6× in 10 rounds 81.3% of the time).
  **But LoopGain helps least here** — the only error signal available is the model's own unreliable
  self-critique, so the signal LoopGain monitors is itself noise.
- **Reliable external oracle** (unit tests, SQL execution, DB-state): iteration mostly helps and
  oscillation is **rarer** (closest proxy CyberCorrect: overshoot ~8–22%, oscillation ~3.6–14.5% —
  *synthetic, prior-calibration only*). **This is LoopGain's sweet spot.**

**Consequence we accept up front:** LoopGain's sweet spot (clean oracle) is the regime where oscillation
is *rarest*. Stage 1 could return a "niche" result, and that is a **valid, reportable outcome** — it
would mean the 90% cost-savings result is the headline and the oscillation-catch is a targeted bonus.
We do NOT chase the high-oscillation no-oracle regime as the headline, because LoopGain cannot reliably
serve it. We measure the verifier-gated regime, honestly, and report whichever world we are in.

## 2. Workloads (Stage 1)

Verifier-gated, realistic difficulty (~30–50% natural one-shot failure on a mid-tier model), each
exposing a clean per-iteration error signal:

| Workload | Task | Oracle (error signal) | Natural failure band | Cost | Stage-1 n |
|---|---|---|---|---|---|
| **BIRD Mini-Dev** (text-to-SQL) | NL→SQL on messy DBs | SQL **execution-match** (binary/graded) | ~30–40% (mid model) | **cheap** | 150–300 |
| **SWE-bench Verified** (subset) | GitHub bug-fix (Python) | **fail-to-pass + pass-to-pass tests** | ~30–40% (mid model) | **expensive (cost driver)** | ~100 |

- **GAIA is excluded**: single-interaction, no native revise loop → weak for a verify-revise study.
- **τ²-bench is deferred to Stage 2** (multi-turn DB-state oracle + pass^k reliability lens).
- **Cheapest pilot (Stage 0)**: BIRD-only at n=150, one model tier — truly minimal spend to sanity-check
  the harness and get a first overshoot signal before committing to the SWE-bench cost.

## 3. Harness & conditions

- **ONE fixed verify-revise driver**, held constant across all cells (no framework confound — the
  re-analysis A3 showed adapters differ in loop dynamics, so framework is a confound to eliminate here,
  not a variable). The driver: generate → run oracle → feed errors back → revise → repeat.
- **`max_iterations = 10`** (generous, so overshoot has room to manifest; a tight cap of 3 mechanically
  hides it). Best-so-far buffer maintained. **Log the full per-iteration error trajectory** `sₜ`.
- **Model tiers: ≥ 2**, run as separate strata (capability swings oscillation by an order of magnitude —
  Llama-3.1-8B 58.8% overturn vs DeepSeek-R1 7.9%). Proposed: one **mid** tier (Haiku-4.5-class) and one
  **frontier** tier (Sonnet/Opus or GPT-5-class). **Never report a single pooled oscillation number.**
- This is a **measurement** bench (base rate), not yet a product comparison. The LoopGain-vs-confidence
  head-to-head is Stage 3, gated on Stage 1–2 showing meaningful overshoot.

## 4. Metrics & pre-registered decision thresholds (FALSIFIABLE — lock before data)

Let `sₜ` = error at iteration `t` (e.g. `1 − fraction_tests_passing`; `1 − sql_exec_match`).
Running best `s* = min_{t′≤t} sₜ′`. Terminal `s_T`. "Good state" = `sₜ ≤ τ` (τ = 0 for binary oracles).

- **FOUND-IT-THEN-BROKE-IT (HEADLINE)**: fraction of tasks that reach a good state at some `t < T` but
  whose **terminal** output is worse (`s_T > s*`). Denominator = tasks that ever reached a good state.
- **Overshoot rate**: after `s*` first reached, some later `sₜ ≥ s* + δ` (δ = 0.1 of error scale, or the
  discrete event "a check that was passing now fails").
- **Oscillation rate**: answer cycles, `ans(yₜ) = ans(yₜ₋₂) ≠ ans(yₜ₋₁)` (CyberCorrect definition).
- **Divergence rate**: `sₜ` trends upward over the last `k` iters with no return to `s*`.

**Pre-registered decision rule (on the HEADLINE metric, per stratum AND pooled-disclosed):**

| Found-it-then-broke-it rate | Verdict | Positioning consequence |
|---|---|---|
| **≥ 15%** | **HEADLINE** | "Catches the divergence a confidence threshold can't" leads the pitch |
| 5–15% | **WORKLOAD-DEPENDENT** | Claim stratified by workload/model; not a universal headline |
| **< 5%** | **NICHE** | Cost-savings (90%) is the headline; oscillation-catch is a targeted bonus |

Anchored to the CyberCorrect 8–22% overshoot prior. The 5–15% band is reported with full stratification,
never collapsed to one number.

## 5. Confounds controlled (the research flagged each)

1. **Verifier reliability (most important).** Weak/flaky oracles manufacture fake "overshoot." Documented:
   SWE-bench weak tests inflate solves (SWE-ABS: ~1 in 5 top-agent "solved" patches semantically wrong;
   78.8% → 62.2% under stronger tests); BIRD strict execution agrees with humans only ~62%.
   **Control**: use the strongest available test suites; **re-confirm every found-it-then-broke-it event
   by re-running the best-state and terminal outputs through the strongest oracle**, and only count the
   degrade if the strong oracle confirms it. Report verifier-agreement rate ALONGSIDE every oscillation
   number.
2. **Model capability.** ≥2 tiers, stratified reporting (see §3).
3. **Framework.** One fixed harness; framework eliminated as a variable.
4. **`max_iterations` cap.** Set to 10 so overshoot can manifest; report sensitivity at the cap.
5. **Selection / difficulty drift.** Pre-declare the task subset and difficulty band before any
   confirmatory run; no optional stopping; dry-run stage-gate at n=10/cell to verify the oracle and the
   difficulty band, amendments timestamped pre-confirmatory-data (BENCH_PROTOCOL amendment discipline).

## 6. Staging

- **Stage 0 (cheapest sanity):** BIRD-only, n≈150, one mid tier. Confirms harness + first overshoot read.
- **Stage 1 (this prereg):** BIRD Mini-Dev + SWE-bench Verified subset, 2 tiers, `max_iter=10`, full
  per-iteration logging. Answers the headline question for verifier-gated loops.
- **Stage 2:** add τ²-bench + a mid tier. Tests two hypotheses: (a) oscillation **rises** with task
  horizon/realism; (b) oscillation **falls** as verifier reliability rises (the Kamoi moderator, directly).
- **Stage 3 (gated on Stage 1–2 showing meaningful overshoot):** LoopGain head-to-head vs.
  "stop when no improvement for k steps," **on the intermittently-improving subset** (the population where
  the differentiated catch should appear). Report catch rate, false-stop rate, tokens saved — mirrors the
  `STABILITY_VS_CONFIDENCE` design but on realistic workloads.

## 7. Open decisions for David (before this is REGISTERED)

1. **Budget cap.** SWE-bench Verified is the cost driver. Approve a $ ceiling, or start Stage 0 (BIRD-only)
   to de-risk for ~nominal cost.
2. **Model tiers.** Which two? (Cost vs. capability-spread tradeoff — at minimum one mid + one frontier.)
3. **Threshold lock.** Confirm the §4 15% / 5% cutoffs (or adjust) — then they freeze.
4. **Scope.** Stage 0 first, or straight to Stage 1?

*Reporting discipline (inherited): never publish a single oscillation number; always stratify by model
tier and verifier reliability — the proxy literature swings both by an order of magnitude. Report a
null/niche result plainly.*
