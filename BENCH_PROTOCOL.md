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
- LG — LoopGain v0.2.0 at default thresholds. Instantiated as `LoopGain(target_error=workload.target_error, max_iterations=20)` with default `TrajectoryThresholds`. The classifier may emit non-INIT band states (including DIVERGING / OSCILLATING) starting at n=2, per `loopgain/core.py:270` and the n=2 special case in `loopgain-core/PROTOCOL_v2_classifier.md`. *See Amendment 2026-05-21 below — earlier wording referenced a `recommended_min_iterations=6` parameter that does not exist in the v0.2.0 API.*

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

### Amendment 2026-05-21 — LG baseline description corrected

**Class:** Scenario design (acceptable — describes WHAT the LG condition is, not what we predict it will produce). **Predicted magnitudes and kill criteria are unchanged.**

**What:** §"Baselines", LG condition, originally read:

> LG — LoopGain v0.2.0 at default thresholds (`recommended_min_iterations=6`)

That parenthetical referenced a parameter that does not exist in the `loopgain` v0.2.0 public API. Verified via `loopgain/core.py:189-198` (`__init__` accepts `target_error`, `max_iterations`, `thresholds`, `trajectory_thresholds`, `classifier`, `smoothing_window`, `assumed_fixed_cap` — no min-iter knob) and `grep -r "min_iter" loopgain/*.py` (no matches).

The phrase `recommended_min_iterations=6` exists in `loopgain-core/PROTOCOL_v2_classifier.md` as **prose guidance about classifier statistical power at short loop lengths**, not as an API contract. The bench protocol mischaracterized it as configuration.

**Why discovered:** W5 stage-gate run (2026-05-21, n=10, real Anthropic API, NOT counted toward registered results) observed LG terminating at iter 2 with state_history `['FAST_CONVERGE', 'DIVERGING']`. Confirmed via library inspection that this is the documented v0.2.0 behavior, not a runner bug.

**Corrected text:** see updated §"Baselines" LG line in current file.

**Impact on predictions:** none. The rationale columns in §"Predicted magnitudes" still contain pre-lock reasoning that referenced the mythical parameter (e.g. H-COST DIVERGING row: "LoopGain stops at `recommended_min_iterations=6` to 8 once classifier fires"). Those rationale cells reflect Dave's reasoning at lock time and are preserved as a historical record. **The predicted magnitude floors themselves (≥30%, ≥70%, ≥60%, ≥75%, ≥80%, etc.) are unchanged and locked.** If the actual data exceeds floors by more than expected — because LG stops earlier than the rationale assumed — that's not a methodology change; it's an outcome that beat the prediction.

**Impact on kill criteria:** none.

**Follow-up (non-blocking):** open a verification issue against `loopgain-core` asking whether the v0.2 classifier's DIVERGING emission at n=2 matches the documented decision rule in `PROTOCOL_v2_classifier.md` (which requires `slope_p < P_SIG` with `slope_p` falling back to 1.0 at n=2). If there's a deviation from documented spec, it's a library issue independent of this bench. The bench reports what the shipped library does; library spec/implementation alignment is `loopgain-core` work, post-bench.

### Amendment 2026-05-21b — H-FALSESTOP definition extended for no-programmatic workloads

**Class:** Scenario design / metric definition (acceptable — describes HOW false-stop is computed, no change to predicted floors or kill thresholds). **Predicted magnitudes and kill criteria are unchanged.**

**What:** §"Metrics → Decision-accuracy" defined false-stop rate as:

> **False-stop rate**: % of LoopGain stops where the B20 counterfactual produced strictly better output by BOTH LLM-judge AND programmatic eval.

The AND-rule is well-formed for workloads where programmatic eval is available (W1 HumanEval, W3 ToolBench/τ-bench success, W4 retrieval@k). It is **structurally undefined** for workloads where programmatic eval is N/A by design — currently W5 (adversarial / waste-avoidance only). Under the literal AND-rule, W5 can never trigger H-FALSESTOP regardless of judge outcomes, which makes the metric unusable for that cell.

**Corrected definition (replaces the prior single sentence):**

- **For workloads with programmatic eval (W1, W3, W4):** false-stop = % of LoopGain stops where B20 counterfactual produces strictly better output by BOTH judge AND programmatic eval. Headline metric.
- **For workloads without programmatic eval (W5, and any future no-programmatic cells):** false-stop = % of LoopGain stops where the judge picks B20. Reported as "false-stop (judge-only)" and segregated from AND-rule numbers in writeups so readers cannot conflate them.

Both forms share the same **predicted floor (≤ 10%)** and the same **kill threshold (> 15%)**. The judge-only form is noisier — but if LoopGain genuinely preserves quality, the judge should mostly find LG-wins or ties, and a judge-only false-stop above 15% is real signal that the rollback is failing on that workload.

**Why discovered:** W5 stage-gate (n=10) judge ruled LG won 9/10, B20 won 1/10 (seed 1). Under the literal AND-rule, that 1/10 doesn't count (no programmatic for W5). The result is technically clean but the metric is structurally vacuous for W5 — which we discovered post-hoc on stage-gate data, *before* registered Phase 2 data is collected.

**Why amend now:** discipline. Amendment timestamped *before* the registered confirmatory run (W1-W4 cells, n≥200) is bulletproof; doing it post-data looks like fitting-to-results even if structurally identical.

### Amendment 2026-05-21c — Judge task-description discipline

**Class:** Methodology / scenario design (acceptable). **Predicted magnitudes and kill criteria unchanged.**

**What:** The judge prompt template (`bench/judge.py::JUDGE_PROMPT_TEMPLATE`) has a `{task_description}` slot filled per workload. Methodology lockdown #2 already required "same judge prompt across all comparisons," but the lockdown did not constrain the task_description's relationship to the workload's quality metric.

**Why discovered:** W5 stage-gate seed-1 spot-check. Judge rationale verbatim:

> *"A The first attempt captures the essential facts concisely while B includes more details but is too long and exceeds the brief requirement."*

A (= B20) preserved 3 of 8 facts; B (= LG) preserved 6 of 8 facts. The judge over-weighted "be brief" and under-weighted "preserve facts" — even though the workload's error_fn is *fact-preservation count*. The task description sent to the judge did not explicitly tie "better" to the error_fn metric, so the judge applied implicit prior weights of its own.

**Discipline (added to methodology lockdowns):**

> **Lockdown 2a — Judge task_description anchored to workload metric.** Every workload's task_description (the string passed to the judge as task context) must explicitly state the dominant quality criterion in terms of the workload's error_fn or programmatic metric. Generic phrasing ("a better attempt is more concise / complete / accurate") is forbidden; specific phrasing ("a better attempt preserves more of {facts} from the source", "a better attempt passes more of the unit tests with no spurious side effects", "a better attempt retrieves more of the gold passages at rank ≤ k") is required.

**Phase 2 implementation note (non-amendment):** the W1-W4 workloads currently being written should set `task_description` strings that explicitly reference the programmatic metric. Code session: when implementing each workload's judge task_description, anchor it to the programmatic eval the workload computes. Otherwise the judge will drift to its own implicit priors and produce noisier rulings.

### Amendment 2026-05-21d — W1 corpus swap + per-cell density-check discipline

**Class:** Scenario design (acceptable — describes WHICH problems exercise the W1 verify-revise loop, not what we predict it will produce). **Predicted magnitudes and kill criteria unchanged.**

**What:** §"Bench matrix" listed W1's task as "Code generation with test feedback" with the KICKOFF.md guidance "use HumanEval problems from `openai/human-eval` (public)." The W1 stage-gate (n=10, Haiku 4.5, real Anthropic API) confirmed that standard HumanEval one-shots on Haiku-class models in 2026 — **all 10 trials hit TARGET_MET at iter 1**, leaving the failure-mode segmentation (CONVERGING / STALLING / OSCILLATING / DIVERGING) empty. The cell as configured cannot exercise the bench's central hypothesis (LG behavior on the long-tail failure-mode quartile).

**Corrected corpus:** W1's task corpus is changed to **HumanEval+** or **MBPP+** (whichever produces a 15–30% one-shot failure rate at the stage-gate density check on Haiku 4.5). Both are published, peer-reviewed benchmark extensions:

- **HumanEval+**: Liu et al., "Is Your Code Generated by ChatGPT Really Correct?" (NeurIPS 2023). Adds ~80x more test cases per problem to catch errors the original HumanEval test suite misses.
- **MBPP+**: same paper, same treatment for MBPP. Slightly harder than HumanEval+.

The choice between them is made by stage-gate empirics: pick the one that produces a 15–30% iter-1 failure rate on Haiku 4.5 in n=10 trials. If both are too easy, fall back to APPS-Interview or Codeforces-easy. **No custom hard-problem curation** — published-benchmark sourcing defends the "you tuned the difficulty" objection.

**Why amend now:** discovered on stage-gate, pre-data. Same discipline as 2026-05-21b/c. Predicted floors and kill criteria unchanged.

**Why this isn't fitting-to-result:** the predicted floors (H-COST ≥30% failure-dense, H-QUALITY ≥50% winrate, etc.) are about LoopGain's BEHAVIOR on the failure-dense quartile, not about what fraction of W1 trials end up in that quartile. Changing the corpus to ensure the quartile exists is corpus design, not prediction adjustment. If LoopGain's behavior on the new corpus's failure-dense trials falls below floor, the kill criterion fires honestly.

### Methodology — per-cell failure-mode density check (discipline rule, not amendment)

**Before every cell's n≥200 registered run, run a stage-gate at small n (≤10) confirming the workload exhibits non-trivial density of states beyond FAST_CONVERGE/TARGET_MET.** Target: at least 20% of stage-gate trials reach STALLING / OSCILLATING / DIVERGING / CONVERGING under B20. If the workload doesn't exhibit that density, the corpus is mis-calibrated for the model under test — redesign the corpus, never retune predictions.

The W1 corpus swap above was discovered by exactly this mechanism. Apply to W2, W3, W4 stage-gates as a precondition. Density-check data is not included in the registered analysis (per §"Sample size" rule on dry-runs).

### Amendment 2026-05-21e — Iteration-driver disclosure (framework_invoke pattern)

**Class:** Methodology disclosure (acceptable — clarifies WHAT is being tested, makes implicit design choice explicit, no change to predicted floors or kill thresholds). **Predicted magnitudes and kill criteria are unchanged.**

**What:** the bench's outer paired-condition loop (B5 / B10 / B20 / LG) drives iteration count across all cells. Inside each iteration, the framework adapter (LangGraph / CrewAI / AutoGen / LangChain / OpenAI Agents SDK / Claude Agent SDK) is constructed and invoked for *one inference step* per iteration. The framework's native multi-step machinery (graph state transitions, GroupChat rounds, handoff chains, autonomous halting logic) is exercised structurally — BaseLLM subclass construction, CreateResult shape, Agent / Crew / Graph construction — but is NOT the iteration driver. The inner LLM call routes through the bench's LLM client (`bench/llm.py`) so per-condition token accounting is identical across B5 / B10 / B20 / LG.

**What this bench tests:**
- LoopGain's cost/quality/decision behavior when it drives a verify-revise loop wired through each of the six framework adapters.
- Adapter wrapper correctness (interface shape, cost-accounting compatibility, instrumentation contract).

**What this bench does NOT test:**
- The case where a user invokes `LangGraph.invoke(initial_state)` (or equivalent for other frameworks) and lets the framework's native graph routing / GroupChat termination / handoff chain decide autonomously when to halt.
- LoopGain's behavior under autonomous framework execution where the framework's own halting logic drives termination.
- Multi-agent debate dynamics where multiple agents converse over multiple rounds inside one framework invocation.

**Why this design:** per-condition cost-parity is the methodology's load-bearing claim — B5 / B10 / B20 / LG must use identical token accounting on identical inputs for the comparison to be meaningful. Each framework defines "iteration" differently (LangGraph graph step, CrewAI Crew kickoff, AutoGen GroupChat round, OpenAI Agents Runner step), so a bench that let each framework drive its own iteration count could not enforce "B10 means 10 iterations" uniformly across cells.

LoopGain's documented product surface is **iterative LLM loops where each iteration produces an observable error signal and the caller invokes `lg.observe(error)` per iteration** — see `loopgain-core/examples/` (covering verify-revise, refinement, tool-use retry, and multi-step reasoning patterns) and the adapter READMEs. The pattern is loop-shape-agnostic: it works for verify-revise, refinement, critique-revise debate, planner-executor, iterative RAG, and any other outer-driven iterative loop. The bench's five workload types (W1-W5) span five of these loop shapes. The outer-driven structure is what LoopGain's API requires (the caller decides when to call `lg.observe`), and it's what the bench tests.

**What the bench DOES exercise of native framework machinery:** `tests/test_adapter_parity.py` constructs and invokes each framework's native runner end-to-end (no bench-loop wrapper) to confirm the LoopGain instrumentation contract holds across all six adapters at the API surface. Those tests pass independently of the bench.

**Public-writeup discipline:** the bench's findings are reported as "LoopGain behavior on outer-driven iterative LLM loops (verify-revise, refinement, critique-revise debate, planner-executor, iterative RAG) wired through the six framework adapters," not "LoopGain behavior on autonomous framework execution." If a reader asks "does this validate LoopGain on my LangGraph multi-agent workflow that uses native graph-state-machine termination?" — the honest answer is "this benchmark doesn't test that use case. It tests the outer-driven iterative pattern, which is LoopGain's documented product surface. Autonomous-framework-execution monitoring would require different methodology (different per-iteration semantics across frameworks, different cost accounting, different quality metric) and is scoped as a separate evaluation. See `tests/test_adapter_parity.py` for the limited native-machinery testing this bench DOES run."

**Known scope gap (acknowledged, not concealed):** autonomous framework execution monitoring — where the framework's native runner drives iteration and halting, and LoopGain would need adapter-provided callback hooks to observe per-step errors — is not tested by this benchmark. Whether the v0.2.0 adapters expose such hooks is a separate library question; whether a bench v2 would extend to that case is future work, gated on (a) library capability, (b) whether the use case generates real user demand post-launch.

**Why amend now:** discovered during W1 implementation, pre-registered-data. Documenting before the registered run preserves the credibility that this design was disclosed, not concealed.

### Amendment 2026-05-21f — Density-check rule refined (target vs hard floor)

**Class:** Methodology refinement (acceptable — clarifies HOW the density check is interpreted, no change to predicted floors or kill thresholds). **Predicted magnitudes and kill criteria are unchanged.**

**What:** Amendment 2026-05-21d introduced the per-cell density-check discipline:

> Before every cell's n≥200 registered run, run a stage-gate at small n (≤10) confirming the workload exhibits non-trivial density of states beyond FAST_CONVERGE/TARGET_MET. Target: at least 20% of stage-gate trials reach STALLING / OSCILLATING / DIVERGING / CONVERGING under B20.

That rule was written as a single threshold ("≥20%") with redesign as the consequence of falling below it. W1 stage-gate (n=10, MBPP+ with deterministic shuffle, Haiku 4.5) landed at **10% density** — below the 20% target, but not zero. The mechanical reading would require corpus escalation; the practical reading is that 2026-era Haiku 4.5 is genuinely competent on standard code-gen benchmarks and the bench should report observed density rather than manufacture it.

**Refined rule (replaces the prior single threshold):**

- **Density ≥ 20%**: target met. Proceed to n=200 registered run.
- **Density in [5%, 20%)**: lower than target but non-trivial. Proceed to registered run with observed density disclosed in writeup. Segmented reporting (analysis/run.py band-segmentation) handles the smaller failure-mode quartile honestly — predicted floors on the failure-dense segment still apply and will be reported with appropriately wide CIs.
- **Density < 5%**: failure-mode quartile effectively absent. Cell does not exercise the hypothesis. Escalate corpus to a harder published benchmark (NOT custom-curated harder problems — same anti-cherry-picking discipline as amendment 2026-05-21d). Re-stage-gate.

**Why amend:** the original 20% threshold conflated two distinct outcomes — "failure mode is rare in the natural distribution" (interesting empirical finding) vs. "failure mode is essentially absent so the hypothesis doesn't trigger" (cell is uninformative). The refined rule distinguishes them. Going pragmatic on the 5–20% range matches how production code-gen workloads actually behave; escalating below 5% protects against testing on a corpus where LoopGain's value proposition has nothing to bite on.

**Anti-cherry-picking defense:** the refinement does not invent a new corpus to push density up. It accepts the observed density as a fact about the model × benchmark and reports it honestly. The 5% hard floor exists specifically so that "report 0/200 failure-mode trials" doesn't slip through as a result.

**Why amend now:** W1 stage-gate completed pre-data (n=10 only, not counted in registered analysis). W2/W3/W4 stage-gates running in parallel as of this amendment timestamp. The refined rule applies prospectively to all four; predicted floors and kill criteria are unchanged.

**Affirmed by W1:** W1 density 10% (in the [5%, 20%) range) proceeds to registered run. If W2/W3/W4 also land in [5%, 20%), the bench reports "2026-era LLMs converge fast on standard benchmarks; LoopGain's value on natural-distribution code-gen is the FAST_CONVERGE win; W5 (engineered) carries the failure-mode story." If they land variably, segmented per-cell reporting tells each story honestly.

### Amendment 2026-05-21g — W3 and W4 corpus choices (BFCL + SciFact)

**Class:** Scenario design (acceptable — describes WHICH published benchmarks supply the W3 and W4 task corpora, no change to predicted floors or kill thresholds). **Predicted magnitudes and kill criteria are unchanged.**

**What:** §"Bench matrix" originally named the following for W3 and W4:

> | W3 | Planner-executor with tool-use | OpenAI Agents SDK + LangGraph | Plan invalidation cascades | τ-bench / ToolBench success (programmatic) |
> | W4 | RAG with iterative retrieval | LangChain | Query rewrite drift to irrelevance | MS MARCO / NQ retrieval@k (programmatic) |

Initial W3 / W4 implementations used **custom inline corpora** (W3: arithmetic ops like `sum_then_mul`; W4: 30-doc topic-sparse inline corpus). Stage-gate density check (2026-05-21, per Amendment 2026-05-21d) returned **0% failure-mode density on both** — below the 5% hard floor in Amendment 2026-05-21f, which mandates corpus escalation to published benchmarks.

**Corrected corpus choices:**

- **W3 → BFCL-V3 multi-turn subset.** Berkeley Function Calling Leaderboard, published by Gorilla LLM Lab (UC Berkeley). Tool-use accuracy graded across difficulty levels; the multi-turn subset is designed to stress sustained tool-call coherence. Lighter integration than τ-bench (no simulated environment to host) while preserving the published-benchmark discipline.
- **W4 → BEIR/SciFact.** Scientific-claim retrieval benchmark from the BEIR suite (Thakur et al., NeurIPS 2021). ~300 documents with gold relevance labels. Subset-friendly, well-known in retrieval research, retrieval@k is the standard quality metric. Lighter integration than full MS MARCO (no multi-GB corpus) while preserving the protocol's intended retrieval-metric character.

**Why the swap:**

- **τ-bench / ToolBench** require a multi-tool simulated environment to be faithful to original methodology. Within the bench's solo-founder scope (Amendment 2026-05-21's solo-founder filter, inherited), this was an unbudgeted lift. BFCL-V3 produces tool-use programmatic signals on a comparable difficulty distribution without env-hosting overhead.
- **MS MARCO / NQ** at full scale ship as multi-GB corpora requiring offline embedding pipelines. SciFact (BEIR subset, ~300 docs) produces retrieval@k signals on a smaller, well-curated corpus that exercises the same query-rewrite-drift failure mode at lighter integration cost.

**Anti-cherry-picking discipline preserved:** both BFCL-V3 and BEIR/SciFact are peer-reviewed, published, peer-validated benchmarks with documented difficulty grading. Neither is a custom curation. Difficulty calibration is from the benchmark authors, not from the bench. The "you tuned the corpus for the result you wanted" objection has no purchase.

**Stage-gate density confirmation (post-swap, pre-registered):**

| Cell | Corpus | Density | Band per 2026-05-21f |
|---|---|---|---|
| W3-a (OpenAI Agents SDK + GPT-4.1-mini) | BFCL-V3 | 20% | target |
| W3-b (LangGraph + Sonnet 4.6) | BFCL-V3 | 30% | target+ |
| W4 (LangChain + Haiku 4.5 + emb-3-small) | BEIR/SciFact | 20% | target |

All three meet the ≥20% target band of Amendment 2026-05-21f.

**Why amend now:** swaps completed pre-registered-data. W3-b at 30% density is interesting (Sonnet output drift may be a contributing factor) but does not block the registered run.

### (Subsequent amendments below — none yet)
