# Bench v2 — Stage 0 Results: Oscillation Base Rate on BIRD (verifier-gated text-to-SQL)

**Pre-registration**: [`BENCH_V2_OSCILLATION_BASERATE_PREREG_DRAFT.md`](./BENCH_V2_OSCILLATION_BASERATE_PREREG_DRAFT.md) — design + frozen decision thresholds (≥15% HEADLINE / 5–15% WORKLOAD-DEPENDENT / <5% NICHE) locked before this run.
**Harness**: [`bench_v2/`](./bench_v2/) (mock-validated; read-only SQL sandbox; per-trial checkpointing).
**Data**: BIRD Mini-Dev (500 questions, 11 SQLite DBs), SHA-256 `aeb211c0…f2be`, from Google CDN.
**Date**: 2026-06-01. **Total API spend: $9.25** (gpt-4.1-mini $0.79 + Sonnet $8.46), inside the $100 cap.

---

## TL;DR

> **The found-it-then-broke-it phenomenon is real and material on realistic, verifier-gated loops — ~1 in 7 — and it is NOT confined to weak models. This rejects the "niche" prior.**
>
> When a verify-revise SQL loop reaches a *correct* answer and keeps iterating ("review and improve"), it **degrades that correct answer back to wrong** in **13–15%** of cases. Both model tiers land in the same range; the **frontier model (Sonnet) is if anything slightly *higher* (15.0%) than the mid model (13.2%)**, not lower. Both 95% CIs sit entirely above the 5% niche floor, so the pre-registered NICHE hypothesis is **rejected** at n=150.

| Tier | n | one-shot success | reached correct | **found-it-then-broke-it** | 95% CI | overshoot | oscillation | divergence | verdict (frozen) |
|---|---:|---:|---:|---:|:---:|---:|---:|---:|:---|
| gpt-4.1-mini (mid) | 150 | 49% | 76 | **10/76 = 13.2%** | [7.3%, 22.6%] | 13 | 22 | 4 | WORKLOAD-DEPENDENT |
| Claude Sonnet (frontier) | 150 | 58% | 100 | **15/100 = 15.0%** | [9.3%, 23.3%] | 24 | 33 | 6 | HEADLINE |
| *pooled (disclosure only)* | 300 | 54% | 176 | *25/176 = 14.2%* | — | 37 | 55 | 10 | — |

**0 task errors, 0 reconfirm-rejections** on either tier (the order-insensitive oracle already prevented ORDER-BY false degrades, so the strong-oracle re-confirmation gate had nothing to strip — every counted degrade is a genuine wrong-result).

---

## Verdict against the frozen thresholds

- By the letter of the pre-registration: **Sonnet = HEADLINE (15.0% ≥ 15%), gpt-4.1-mini = WORKLOAD-DEPENDENT (13.2%)**.
- **The honest joint reading:** the two tiers are **statistically indistinguishable** — CIs [7.3, 22.6] and [9.3, 23.3] overlap almost completely and both straddle the 15% line. n=150 is not enough to place the rate precisely relative to the 15% boundary. **Do not claim "frontier models oscillate more."** The defensible statement is: **the rate is ~13–15% across both tiers, at the WORKLOAD-DEPENDENT/HEADLINE boundary, and decisively above NICHE.**
- The one thing n=150 **does** settle cleanly: **NICHE (<5%) is rejected** — both lower CI bounds (7.3%, 9.3%) exceed 5%.

This **contradicts the prior** stated in the prereg (§1) that LoopGain's clean-oracle sweet spot would be where oscillation is rarest. On BIRD it is not rare.

---

## What it means (and the load-bearing caveat)

**The result validates the product mechanism — conditionally, and the condition matters.**

This measures loops that **keep iterating after reaching a correct answer** (the harness has no stop-at-success short-circuit, by design — mirroring the v1 `B20` substrate — because that is the only way to *observe* the degrade). The finding is therefore precisely:

> *For verify-revise loops that don't reliably stop when they're already correct, ~1 in 7 correct answers get destroyed by continued "improvement."*

That population — loops that iterate past success — **is exactly what LoopGain's `TARGET_MET` + best-so-far rollback is built to protect.** So Stage 0 says the protection addresses a **real, ~14%-frequency failure on a realistic clean-oracle workload**, not a rare edge case. It pairs directly with the re-analysis finding ([`STABILITY_VS_CONFIDENCE_RESULTS.md`](./STABILITY_VS_CONFIDENCE_RESULTS.md), Claim 2 PASS) that oscillation detection catches waste a confidence/plateau rule structurally misses.

**Conversely (the honest limit):** a loop that *does* stop at first success would not exhibit this — there's nothing to break. So the claim is "for refine-past-success loops," not "for all loops." Measuring how common refine-past-success loops are in production is the Part-A question the research left open.

**Distribution detail:** degrades occur across *all* difficulty bands, including `simple` (gpt 3/24, Sonnet 5/29) — not just hard tasks. So it's not "only the hard queries flap"; even easy correct answers get over-edited into wrongness.

---

## Limitations (disclosed)

- **n=150/tier, single epoch.** CIs are wide (±~7 pp). The 13.2 vs 15.0 split is noise; treat the finding as "~14% ± a lot," not a point estimate. Stage-1-scale n would tighten it.
- **Simple harness prompt** (zero-shot, schema + evidence, no few-shot exemplars). One-shot success (49%/58%) is below tuned BIRD-leaderboard numbers — the harness is weaker than a production SQL agent. This lowers absolute accuracy but does **not** bias the *FTB rate among reached-good loops*, which is the metric of interest (it's conditional on having reached correct).
- **The "review and improve" critique after success is a generic prompt.** A different post-success critique could raise or lower the degrade rate. We used one fixed, plausible refinement prompt; we did not tune it.
- **BIRD-only (Stage 0).** SWE-bench Verified (the other prereg workload) was deferred for cost + infra; whether code-gen-with-tests shows the same rate is untested.
- **Runaway-query artifact handled:** some model queries were pathological (cartesian joins); the oracle's 5s execution watchdog aborts them as failed attempts, so they don't hang the run or distort the metric (they just count as a wrong iteration — which is correct).

---

## Reproducibility

- `bench_v2/` harness; `.venv/bin/python -m bench_v2.runner --source bird --bird-root <BIRD> --provider {openai,anthropic} --model {gpt-4.1-mini,claude-sonnet-4-6} --n 150 --max-iter 10 --i-understand-this-spends-money`
- Deterministic 150-task sample (seed 20260531), same tasks both tiers.
- Full per-iteration trajectories: `data/results/v2_bird_{gpt41mini,sonnet}.json`.
- Frozen decision thresholds were not edited after seeing results; the WORKLOAD-DEPENDENT/HEADLINE boundary outcome is reported as-is, with CIs, rather than rounded to a cleaner story.

---

## Appendix A (EXPLORATORY) — Can an oracle-free output-stability signal recover the degrades?

**Status: EXPLORATORY, not pre-registered.** Post-hoc analysis on the existing Stage-0 trajectories,
to test a product idea: could LoopGain run with **no user-defined error function** — keying only on the
*output trajectory* (how the result changes across iterations) — and still catch the found-it-then-broke-it
degrades? An untuned heuristic, n=25 degrade cases, BIRD-only, exact result-set identity. Directional only.

**Setup.** Each rule sees only the per-iteration **result identity** (a hash of the result set) — never the
gold answer. We then score what the rule *would have shipped* against ground truth.

| Rule (oracle-free) | FTB degrades recovered | clean loops preserved |
|---|---:|---:|
| ship last output (naive run-to-end) | 0/25 (0%) | 151/151 (100%) |
| majority-vote on result | 11/25 (44%) | 149/151 (99%) |
| **first-recurring result** | **16/25 (64%)** | 148/151 (98%) |
| first-stable (held 2 iters) | 10/25 (40%) | 149/151 (99%) |
| *oracle best-so-far (needs error signal — ceiling)* | *25/25 (100%)* | *(100%)* |

**Net:** the best oracle-free rule ("ship the first output that recurs") raises correct-answers-shipped
across all 176 reached-correct loops from **151/176 (86%, naive) → 164/176 (93%)** — with **no error
definition and no verifier** — closing about half the gap to the perfect-oracle ceiling.

**Why it works, and the hard limit.** 18/25 degrade trajectories are **cyclic** (the correct result
re-appears, median 3×), so voting/recurrence lands on it without knowing it's correct. The other **7/25 are
one-way drift** (correct produced once, then lost) — **structurally unrecoverable without a verifier**.
So the oracle-free ceiling on this workload is ~72%; the heuristic got 64%.

**What it does and does not support.** It supports offering a **zero-config "no error function" mode** that
delivers the dynamics half (oscillation/divergence catch + rollback) for most degrades. It does **not**
support claiming correctness: output-stability can settle on a *stable wrong* answer, and the 98–99%
clean-preservation here is BIRD- and SQL-specific (exact result identity). For free-text/code outputs the
signal would be fuzzier (embedding/edit-distance) and likely recover less. **Correctness still requires a
real error signal** (which recovers 100%). Treat zero-config as a low-friction on-ramp with explicit limits,
not a verifier replacement. Needs Stage-1-scale n and a second workload before it informs any public claim.
