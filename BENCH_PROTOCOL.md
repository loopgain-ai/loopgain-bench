# LoopGain Bench — Pre-Registration

**Status:** REGISTERED — locked before any real-data cell runs.
**Date locked:** 2026-05-21
**Author:** Dave Fitzsimmons
**Bench version under test:** `loopgain` v0.2.0 (PyPI, 2026-05-18)

## Companion documents

- `loopgain-core/PROTOCOL_v2_classifier.md` — pre-registration template + classifier design
- `loopgain-core/RESULTS_v2_classifier.md` — Tier 1–3 classifier validation (the conservative-classifier tradeoff is documented here)
- `loopgain-core/examples/README.md` — existing per-task baseline-vs-LoopGain demos (cell templates)
- `loopgain/wiki/advisor-sessions/loopgain-bench-design.md` — session design notes
- `loopgain/wiki/concepts/loopgain-decision-engine.md` — five Aβ threshold bands
- `loopgain/wiki/concepts/loopgain-dashboard-observability.md` — dashboard spec

## What this bench measures

The classifier validation (PROTOCOL_v2) answered: *does the classifier correctly label loop states?* Answer: yes on math (98.8% mocks), partially on real-LLM at n=30 (77% conditional, conservative diverging-recall by design).

This bench answers a different question: *when LoopGain replaces `max_iter=N` in a real agentic loop, what happens to **cost**, **iterations**, **wall-clock**, and **output quality**, across the major framework ecosystems, at statistically meaningful N?*

The bench does **not** re-validate the classifier math. Tier 1 and Tier A of PROTOCOL_v2 are sufficient evidence for that. The bench tests the *operational* claim: that the classifier's decisions, applied to real agentic loops via the six framework adapters, produce measurable improvements over the `max_iter=N` baseline without sacrificing output quality.

## Hypotheses (pre-registered, falsifiable)

H-COST. On loops with non-trivial failure-mode density (≥30% of trials exhibiting STALLING / OSCILLATING / DIVERGING under `max_iter=20`), LoopGain reduces median $-per-task by ≥ 30% vs `max_iter=10` baseline, segmented by failure-mode.

H-ITERS. LoopGain reduces median iterations-per-task by ≥ 30% on the failure-mode-dense quartile of workloads vs `max_iter=10`.

H-QUALITY. LoopGain output quality is preserved within 5 percentage points of `max_iter=20` baseline output quality, measured by:
  - (a) LLM-judge pairwise winrate (LoopGain output ≥ 50% — null is 50%); AND
  - (b) programmatic eval delta (where available: HumanEval / ToolBench / MS MARCO retrieval@k) within 5 pp.

H-EARLYWARN. For loops that catastrophically diverge under `max_iter=20` (defined: final E_ratio > 2.0), LoopGain flags STALLING / OSCILLATING / DIVERGING with median lead time ≥ 3 iterations before the catastrophic point.

H-DECISION-ACC. Band-state forward-prediction accuracy: when LoopGain emits CONVERGING at iteration k, the loop continues converging through iteration k+3 in ≥ 75% of cases. When LoopGain emits DIVERGING, additional iterations to `max_iter=20` ceiling do not produce measurably better output (LLM-judge winrate of B20-extended vs LoopGain rolled-back output ≤ 20% — i.e. extension wins less than 20% of the time).

H-FALSESTOP. False-stop rate (defined: % of LoopGain stops where `max_iter=20` counterfactual produced measurably better output via LLM-judge AND programmatic eval) ≤ 10%.

H-FRAMEWORK-PARITY. Per-adapter results are within 5 pp of each other on the same task type (no adapter is broken at scale; results generalize across LangGraph / CrewAI / AutoGen / LangChain / OpenAI Agents SDK / Claude Agent SDK).

## Bench matrix

### Workloads (5 task types — selected for failure-mode density)

| ID | Task | Framework(s) | Failure-mode bias | Quality signal |
|---|---|---|---|---|
| W1 | Code generation with test feedback | LangGraph + Claude Agent SDK | Compiler/test-error oscillation | HumanEval / MBPP pass rate (programmatic) |
| W2 | Multi-agent critique-revise | AutoGen + CrewAI | Critic-writer lockup | LLM-judge pairwise + task rubric |
| W3 | Planner-executor with tool-use | OpenAI Agents SDK + LangGraph | Plan invalidation cascades | τ-bench / ToolBench success (programmatic) |
| W4 | RAG with iterative retrieval | LangChain | Query rewrite drift | MS MARCO / NQ retrieval@k (programmatic) |
| W5 | Adversarial / known-bad inputs | All frameworks (rotated) | Engineered for divergence/oscillation | n/a — waste-avoidance only |

Total cells (workload × framework): 10 measurement cells.

### Baselines

For every cell, every workload is run against four conditions, with identical seeds, prompts, models, and starting state:

- B5 — `max_iter=5` (popular LangChain/CrewAI default)
- B10 — `max_iter=10` (LangGraph default)
- B20 — `max_iter=20` (production-cautious; serves as ground-truth oracle for what an unconstrained run produces)
- LG — LoopGain v0.2.0 at default thresholds (`recommended_min_iterations=6`)

### Sample size

- **n ≥ 200 trials per cell** (workload × framework). 10 cells × 200 trials × 4 conditions = 8,000 loop runs.
- Each trial runs all four conditions on the SAME prompt with the SAME seed. Trial is the unit of randomization; condition is paired within trial.
- Dry-run at n=10 per cell first to catch adapter bugs and methodology holes cheap. Dry-run data is **not** included in the registered analysis.

### Models

Mix selected for cross-vendor + cross-tier coverage. Same model used across all four conditions within a cell.

| Workload | Model | Rationale |
|---|---|---|
| W1 (code) | Claude Haiku 4.5 | Cost-efficient; ~90% of Sonnet capability on code; shows LoopGain works on smaller models (the harder demo) |
| W2 (debate) | GPT-4.1-mini | Cross-vendor coverage; debate doesn't need top-tier capability; defends "works on OpenAI too" |
| W3 (planner) | GPT-4.1-mini (OpenAI Agents SDK adapter) + Claude Sonnet 4.6 (LangGraph adapter) | Native model per framework; cross-framework parity test doubles as cross-model test |
| W4 (RAG) | Claude Haiku 4.5 (completion) + text-embedding-3-small (embedding) | Cost-efficient completion + standard embedding |
| W5 (adversarial) | Claude Haiku 4.5 | Engineered adversarial inputs don't need premium model |

Total API spend estimate: ~$30–60 for the 8,000-loop run.

## Metrics (definitions, locked)

### Cost
- **Token cost (USD)**: input_tokens × provider_input_price + output_tokens × provider_output_price, per published rates as of the run date. Provider rates frozen at bench start (snapshot to `prices.json` in repo). No prompt-caching discounts unless prompt caching is actually enabled (and disclosed).

### Iterations
- **Iterations per task**: count of revise steps. Single-shot success counts as 1.

### Wall-clock
- **Wall-clock per task (s)**: end-to-end including network. Recorded but not the headline; LoopGain's value is cost+quality not speed.

### Quality
- **LLM-judge pairwise winrate**: blinded pairwise comparison, position-randomized. Judge model is DIFFERENT from the loop's model (e.g. Sonnet judges Haiku output, GPT judges Anthropic output, and vice versa, on a rotating schedule). Judge prompt is identical across all comparisons. Tie counts as 0.5. Reported as % wins for LoopGain output vs B20 output.
- **Programmatic eval**: where available (HumanEval, MBPP, ToolBench, MS MARCO), report pass rate / success rate / retrieval@k delta vs B20.

### Decision-accuracy
- **Early-warning lead time**: for loops where B20 produces E_ratio > 2.0 at iter 20, the iteration at which LoopGain first emits STALLING / OSCILLATING / DIVERGING. Reported as median + distribution.
- **Band-state forward-prediction accuracy**: when LoopGain emits CONVERGING at iter k, does the loop continue to converge (E decreasing) through iter k+3? When DIVERGING, would iters k+1..20 produce better output (LLM-judge)?
- **False-stop rate**: % of LoopGain stops where the B20 counterfactual produced strictly better output by BOTH LLM-judge AND programmatic eval.

## Methodology integrity (locked-in safeguards)

These are non-negotiable and must hold across every cell. Any deviation invalidates the cell.

1. **Token cost honesty.** Real provider pricing snapshot in `prices.json` committed at bench start. No prompt-caching discounts unless caching is enabled and disclosed.
2. **Judge model ≠ loop model.** Always cross-model judging. Same judge prompt across all comparisons.
3. **Pairwise position-randomized.** LoopGain output appears in position A in 50% of comparisons, position B in 50%. Random assignment seeded.
4. **Same seeds across conditions.** Within a trial, all four conditions get the same prompt, same model, same seed (where models support seeding), same starting state. Trial is the unit of randomization.
5. **No mid-run filtering.** Failed trials (model errors, network errors, malformed output) are flagged and reported but NOT silently dropped. Either reported with the failure, or the trial is re-run *with a new seed* (documented).
6. **Sample size committed before data lands.** n ≥ 200 per cell, declared up front. No optional stopping. No "let me add 100 more" after seeing a borderline result.
7. **Same wall-clock environment.** Trials run back-to-back across all four conditions, same machine, same network, same time window. No comparing a 2am LoopGain run to a 2pm baseline run.
8. **Pre-registration committed to repo BEFORE first real-data cell.** This file, with all values filled in, committed and timestamped (2026-05-21) before any cell beyond the n=10 dry-run captures real data.
9. **Raw data immutable.** Once `data/raw/` for a cell is written, it is git-committed and never modified. Analysis runs on the raw data; if analysis needs to be redone, it redoes from raw, never re-collects.
10. **Analysis plan declared upfront.** Segmentation by failure-mode (FAST_CONVERGE / CONVERGING / STALLING / OSCILLATING / DIVERGING), per-cell + aggregate, with the segmentation rule locked here, not chosen post-hoc.

## Predicted magnitudes (locked 2026-05-21)

Predictions derived from theoretical reasoning + prior data (examples runs, Tier 3 results). Specificity is the discipline.

| Hypothesis | Quantity | Prediction | Reasoning |
|---|---|---|---|
| H-COST (failure-dense quartile, vs B10) | median $ reduction | ≥ 30% | Stalling/diverging trials run to B10 cap (10 iters); LoopGain stops ~iter 6–8 with conservative classifier behavior. 30% is the floor accounting for Tier-3 conservatism. |
| H-COST (FAST_CONVERGE segment, vs B10) | median $ reduction | ≥ 70% | Example 01 shows 80% on TARGET_MET (1 vs 5 iters). FAST_CONVERGE typically hits in 1–2 iters; B10 always runs to 10. 70% is conservative. |
| H-COST (DIVERGING segment, vs B20) | median $ reduction | ≥ 60% | LoopGain stops at `recommended_min_iterations=6` to 8 once classifier fires. B20 runs to 20. 60% = stop by iter 8. |
| H-ITERS (failure-dense quartile, vs B10) | median iters reduction | ≥ 30% | Tracks cost reduction; cost ≈ iters × tokens_per_iter with tokens_per_iter relatively stable per workload. |
| H-QUALITY | LLM-judge winrate (LoopGain vs B20) | ≥ 50% | Preservation claim, not improvement. Null = 50%. Bootstrap CI must not significantly exclude 50%. |
| H-QUALITY | programmatic eval delta vs B20 | within 5 pp | Best-so-far rollback should make this tight. 5 pp is credible upper bound. |
| H-EARLYWARN | median lead time | ≥ 3 iters | Honest guess. Classifier conservatism (Tier-3) suggests less lead than pure math predicts. If catastrophic point at iter ~12 and LoopGain flags at iter ~6–8, lead time = 4–6. Floor 3. |
| H-DECISION-ACC | CONVERGING → continues-converging accuracy | ≥ 75% | Tier-3 conditional was 77%; mocks 100%. 75% leaves headroom without being trivially passable. |
| H-DECISION-ACC | DIVERGING → no-better-output rate | ≥ 80% | When LoopGain flags DIVERGING, additional iters to B20 shouldn't produce better output. If the flag is meaningful, this is high. |
| H-FALSESTOP | false-stop rate | ≤ 10% | KILL METRIC — the most important number. Best-so-far rollback should keep this low. No prior data; bench discovers the real number. |
| H-FRAMEWORK-PARITY | inter-adapter spread on same task | ≤ 5 pp | Adapters wrap same core. No data — smoke tests pass at n=1. This is the slot where bench surfaces a silently-broken adapter. |

## Kill criteria (locked 2026-05-21)

A kill criterion firing does NOT mean "kill the project." It means: ship the version that hit the limitation, with the documented tradeoff publicly disclosed, AND any proposed fix must be re-validated on a fresh run (new seed list, new prompts, separate `data/raw/`), not on the data that flagged the failure.

| Criterion | Threshold | Action if fired |
|---|---|---|
| False-stop rate on any cell | > 15% | Ship LoopGain v0.2.0 with documented limitation. Fix work proceeds on separate branch + fresh validation set. |
| Quality preservation (LLM-judge winrate vs B20) | < 40% on any cell | Quality claim doesn't hold for that cell; rollback heuristic needs work; ship with disclosure. |
| Cost savings on failure-dense quartile (vs B10) | < 10% | Product claim ("replaces `max_iter=N`") doesn't hold quantitatively; revise headline framing to match observed effect size. |
| Early-warning lead time on diverging loops | < 1 iter (median) | "Early warning" claim is bunk on real loops; soften Barkhausen public framing. Math is correct; operational claim must match data. |
| Adapter parity spread | > 15 pp | Underperforming adapter ships with known-limitation note in its README; not silently included in headline numbers. |

## Hero-story selection (mechanical, not editorial)

Among all 8,000 trial runs, the hero story for DM payload / blog screenshot / `loopgain.ai/benchmarks` hero image is the trial maximizing:

```
score = ($_cost_B20 - $_cost_LG) × (1 - judge_loss_prob_LG_vs_B20)
```

Top candidate by score wins. Ties within 10% of top score: tiebreaker is cleanest convergence-profile shape for screenshot legibility (subjective, declared up front). No other editorial selection.

## Pre-registered analysis plan

For every cell, the analysis script (`analysis/run.py`, committed before data collection) produces:

1. Per-condition aggregate stats: $ median + IQR, iters median + IQR, wall-clock median + IQR.
2. Quality comparison: LLM-judge winrate LoopGain vs B20 with 95% bootstrap CI; programmatic eval pass-rate delta with 95% CI.
3. Failure-mode segmentation: stats above, segmented by LoopGain's emitted band at trial end (per the v0.2 classifier).
4. False-stop accounting: trial-level table of stops + B20 counterfactual outcomes.
5. Early-warning lead-time distribution: for diverging trials, histogram of first-flag iteration vs catastrophic-point iteration.
6. Adapter parity comparison: same task across adapters, stats above.

Aggregate tables go on `loopgain.ai/benchmarks`. Segmented tables go in the blog post and `loopgain-bench/README.md`. Raw data is published under `data/raw/`.

## What happens if a kill criterion fires

Two-step workflow, both required:

1. **Document honestly.** The failure is reported in the bench writeup at full strength. The conservative-classifier diverging-recall story in `RESULTS_v2_classifier.md` is the template: surface the failure, diagnose root cause, name the tradeoff, state what's shipping with the limitation.

2. **If a fix is proposed, validate on FRESH data.** A new seed list, new prompts (or same task type with new instances), separate `data/raw/`. The classifier OR-gate prototype in RESULTS_v2 was correctly NOT shipped because it was tested on the same data used to diagnose the issue. Same rule here: validation on the same data that flagged the failure is p-hacking. Hold the line.

The bench writeup may include "v0.2.0 ships with limitation X; v0.3 prototype tested on fresh data N=200 shows the fix and is gated behind config flag Y." That's the credible path. "We tried 6 variants on the same data until one passed" is not.

## Limitations to disclose in the writeup

(Pre-acknowledged — extend after data lands if new ones surface.)

- **n=8-iteration t-test power.** Same constraint as PROTOCOL_v2: slope significance on short loops has irreducible Type-I error. Tier-3 already documents this; the bench inherits it.
- **Adversarial-workload selection bias.** W5 (adversarial inputs) is constructed to fail. The bench measures *how much* LoopGain saves on engineered failures; it does not claim the engineered failure rate matches naturally-occurring production rates. Report each separately.
- **LLM-judge is noisy.** Judge winrates have inherent variance. Report 95% CIs, not point estimates. Cross-model judging mitigates but does not eliminate.
- **Pricing snapshot.** Provider prices change. Cost numbers are valid as of the snapshot in `prices.json`.
- **Single bench run per cell.** N=200 trials per cell, but only one collection epoch. Production traffic over months may behave differently.

## Amendments

Amendments to this protocol AFTER 2026-05-21 lock require:
- A dated entry below describing the amendment and reason.
- A statement of whether the amendment is to *scenario design* (acceptable) or to *predicted magnitudes / kill criteria* (NOT acceptable post-data — that's p-hacking).
- The PROTOCOL_v2 amendment of 2026-05-18 (Tier-3 converging spec replacement, pre-confirmatory) is the model: scenario design fixed, classifier untouched, amendment timestamped before confirmatory data.

### (Amendments below — none yet)
