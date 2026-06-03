# Results — Stability-Based Stopping vs Confidence-Based Early Termination

**Pre-registration**: [`STABILITY_VS_CONFIDENCE_PREREG.md`](./STABILITY_VS_CONFIDENCE_PREREG.md) — committed (`45c45f1`) **before** any number below was computed. The two falsifiable bars in §3–§4 of that doc were **not** edited after results were seen.
**Substrate**: the immutable registered JSONL (`data/raw/w*-registered.jsonl`, 2,000 trials / 8,000 loop runs). **Zero new API spend** — this re-reads per-iteration trajectory data already captured.
**Analysis script**: [`analysis/stability_vs_confidence.py`](./analysis/stability_vs_confidence.py) (reproducible, reads only disk; results dumped to `data/results/stability_vs_confidence.json`).
**Date**: 2026-05-31.

---

## TL;DR (the honest verdict)

> **Stability does NOT beat confidence on raw savings — but oscillation detection catches a real slice of waste that a confidence rule structurally cannot see. By the pre-registered decision rule the result is NULL on savings (Claim 1) and PASS on oscillation-catch (Claim 2): the two signals are complementary, not a "stability wins" outcome.**
>
> - **Claim 1 (stability beats confidence on savings at matched quality): NULL → HYBRID.** A stability stop saves only **+1.54 pp** more token-$ than a matched confidence/plateau baseline overall (95% CI [+1.45, +1.64]) — real and positive, but well under the pre-registered **+5 pp** bar, and the margin flips with the confidence rule's patience knob (k=1: −0.05 pp; k=2: +1.54 pp; k=3: +3.05 pp). On the failure-dense W5 family the gap widens to **+4.15 pp** but still doesn't clear +5 pp. Both rules sit squarely in the literature's 20–55% band; here both save **~88–95%** vs `max_iter=20`. **The level signal already captures most of the savings.**
> - **Claim 2 (oscillation detection catches unique waste): PASS.** Oscillation-band stops uniquely catch **21.1%** of the wasted iterations a confidence-only rule lets run (pre-registered bar: > 15%). Divergence catches another 27.6%; the dynamics signal collectively catches **48.6%** of confidence-missed waste. This effect concentrates in the oscillating loops where the error keeps *intermittently* improving, so a plateau/patience rule never trips.
>
> **Synthesis**: confidence-based early-exit and stability-based stopping are **complementary, not competing** — confidence captures the bulk of the savings; stability adds the oscillation/divergence cases the level signal misses. Combining both signals catches strictly more wasted iterations than either alone.

This mirrors the original bench's discipline: two pre-registered floors were disclosed as MISSED in [`RESULTS.md`](./RESULTS.md); here, Claim 1 is reported as a clean NULL against its own bar rather than spun as a win.

---

## Method recap (see prereg §1–§2 for the full spec)

- The bench ran **loopgain 0.2.0** (legacy single-feature classifier: `Aβ_smooth` = EMA of `Eₙ/Eₙ₋₁`, window 3; bands 0.3/0.85/0.95/1.05; terminal on TARGET_MET / OSCILLATING / DIVERGING / MAX_ITER). Replaying that logic on each trial's recorded `LG.error_history` with the correct **per-cell `target_error`** (`0.0` for W1–W4, **`None` for W5**) and `max_iterations=20` reproduces the recorded outcome, iteration count, full state history, **and** gain margin for **2,000/2,000 trials (100.0%)**. The replicated stop rule **is** the product.
- `B20` is a pure fixed-cap (always 20 iters, no short-circuit), so it preserves the complete unstopped trajectory. **Both** rules are replayed on the identical B20 trajectory; the only difference is the early-stop trigger:
  - **STABILITY** = the verified 0.2.0 logic (dynamics: `Aβ_smooth` bands).
  - **CONFIDENCE** = patience/plateau on the error level (stop when best-so-far hasn't improved for `k` iters, or target met). Same per-cell target, same best-so-far buffer, same ceiling. Primary `k=2`; sensitivity `k∈{1,2,3}`.
- Savings reported as **% token-$ saved vs B20**, using a per-trial linear cost model fit to the recorded `(5,$B5),(10,$B10),(20,$B20)` staircase against the frozen `prices.json`. 0 degenerate-cost trials. Iterations-saved is the exact underlying unit.
- All CIs are paired-by-trial bootstrap (10,000 resamples, seed 20260531).

---

## Claim 1 — Stability vs confidence on savings at matched quality → **NULL → HYBRID**

| Subset | n | mean savings, STABILITY | mean savings, CONFIDENCE | **ΔS (stab − conf)** | 95% CI on ΔS | ΔQ (norm. err, >0 = stability worse) | Verdict |
|---|---:|---:|---:|---:|:---:|---:|:---|
| **Overall** | 2,000 | 89.62% | 88.08% | **+1.54 pp** | [+1.45, +1.64] | +0.0155 (CI ⊂ ≤0.05) | **NULL → HYBRID** |
| W1–W4 (natural) | 1,400 | 95.13% | 94.71% | +0.42 pp | [+0.35, +0.50] | +0.0221 | NULL → HYBRID |
| W5 (adversarial) | 600 | 76.77% | 72.62% | +4.15 pp | [+4.06, +4.23] | +0.0000 | NULL → HYBRID |

**Pre-registered bar (prereg §3): PASS requires mean ΔS ≥ +5 pp AND CI excludes 0 AND quality non-inferior.** The CI excludes 0 (stability *is* reliably a hair cheaper) and quality non-inferiority holds (stability's kept output is at most ~0.02 normalized-error-units worse, well inside the +0.05 margin) — **but the savings margin never reaches +5 pp**, so the result is **NULL → HYBRID** by the pre-registered rule, exactly the "savings indistinguishable at matched quality" branch.

**The margin is a tuning artifact, not a structural edge.** Patience sensitivity (overall mean ΔS):

| Confidence patience `k` | mean ΔS |
|---|---:|
| k = 1 (aggressive confidence) | **−0.05 pp** (confidence ties/edges stability) |
| k = 2 (default) | +1.54 pp |
| k = 3 (lenient confidence) | +3.05 pp |

Whether stability "wins" on savings flips on a single knob of the *baseline*. That is the definition of "same regime." Both rules live in the literature's 20–55%-savings band — on this corpus both deliver ~88–95% vs `max_iter=20`, because the dominant savings driver (TARGET_MET / fast convergence on W1–W4) is **shared** by both rules.

**Why W5 is where any gap lives**: on W1–W4 both rules short-circuit at error 0, so they stop at nearly the same iteration (ΔS = +0.42 pp). Only on W5 (no target, the model degrades a good output) does the *dynamics* signal pull ahead (+4.15 pp) — but even there, not by the pre-registered margin.

---

## Claim 2 — Oscillation detection catches unique waste → **PASS**

Wasted iteration (under the confidence rule) = an iteration executed *after* the confidence rule had already observed its kept-best output (pure cost, no quality gain). Uniquely-caught = wasted iterations that an OSCILLATING-band stop cuts because it fired earlier than the confidence rule would.

| Quantity | Value | Notes |
|---|---:|---|
| `W_total` — total wasted iters under confidence | **1,368** | W5: 1,200 · W1–W4: 168 (88% in the failure-dense family) |
| `U_osc` — uniquely caught by **OSCILLATING** stop | **288** | **21.1% of `W_total`** |
| `U_div` — uniquely caught by DIVERGING stop (context) | 377 | 27.6% |
| `U_all` — uniquely caught by any stability stop (context) | 665 | **48.6%** |

**Pre-registered bar (prereg §4): PASS if `U_osc / W_total` > 15%.** Observed **21.1% > 15% → PASS.** (Weak-pass band 10–15% not needed; FAIL band ≤10% not hit.)

**What this means mechanically**: the cases oscillation detection uniquely catches are loops where the error keeps *intermittently* improving (down-up-down) — so a plateau/patience rule keeps resetting its no-improvement counter and never stops, while the `Aβ_smooth` oscillation signal sees the flapping and stops. This is the structural blind spot of any level/confidence signal, and it is exactly the slice the stability signal owns. It is real (21.1%) but concentrated in W5; on natural-distribution workloads the confidence rule already catches nearly everything.

**Claims 1 and 2 are consistent, not contradictory.** The unique waste oscillation catches (Claim 2) is real but small in aggregate token-$ terms (it's ~0.5 iter/trial on cheap W5 trials), so it does not move the overall savings margin past +5 pp (Claim 1). Stability's contribution is a *targeted catch*, not a *broad savings win*.

---

## Diagnostic analyses

### A1 — Within-run stationarity: the instantaneous gain signal is low-SNR and non-stationary

Substrate = B20 full 20-iter trajectories.

- **48.4%** of trajectories are **flat** (constant error — the stop decision is trivial; the loop either one-shot it or never moved). **51.6%** actually vary (a real decision exists).
- Among varying trajectories (n=1,032):
  - **Step SNR** `|mean Δlog₁₀E| / std(Δlog₁₀E)`: median **0.236**, and **0.0%** of runs have SNR > 1. The per-iteration trend is essentially **always smaller than the per-iteration noise**.
  - **Aβ lag-1 autocorrelation**: median **−0.081** (near zero, slightly anti-persistent) — the step process looks white/flappy, not a smooth trend.
  - **Variance ratio (2nd half / 1st half)**: median **0.023** — variance *collapses* over the run; the signal is **not** variance-stationary.

**Interpretation**: a stop decision based on the *instantaneous* smoothed gain `Aβ_smooth` is operating near the noise floor on real loops — the dynamics are jump-dominated, not trend-dominated. This is a principled motivation for the **0.3.0 multi-feature trajectory classifier** (which keys on full-history slope significance + detrended residual std rather than the instantaneous ratio). The `osc_std` library feature reported a median of 0.033 but a mean of 1.114 — that mean is inflated by `E=0` crossings (the `log₁₀` floor `1e-12 → −12` spike); it is the literal feature but a poor stationarity descriptor, which is why the robust descriptors above are reported alongside.

### A2 — Step jitter: the smoothing window is *not* the lever; premature stops are the real risk

- Iteration-to-iteration jitter is jump-dominated: `|ΔAβ|` median 0.000 but mean **28.0** (heavy-tailed — explosive ratios when a small error precedes a large one); `|Δlog₁₀E|` median 0.000, mean 1.12.
- Re-running the stability stop rule with EMA windows **{1, 3, 5, 7}** changes the stop iteration in **< 1% of trials** at every step. **Median stop iter = 1 and the premature/late counts are identical across all four windows.**

**Interpretation**: more smoothing does **not** stabilize the stop decision on this corpus — the decision is driven by discrete error jumps that survive any reasonable EMA, not by smoothable high-frequency noise. So "tune the smoothing window" is the wrong knob; a different *signal class* (trajectory features, A1) is the right lever. The genuine risk surface is **premature stops: 3.7%** of trials stop before the trajectory's global-best iteration is reached (≈ the bench's documented AND-rule false-stop magnitude) and **0.9%** stop ≥2 iters late.

### A3 — Cross-framework consistency: stop *decisions* agree, but LangGraph's loop *dynamics* differ (candidate root-cause for the 5.8 pp parity spread)

Per-framework medians of the replayed signal:

| Framework | n | med stop iter | med gain margin | dominant outcomes |
|---|---:|---:|---:|---|
| autogen | 200 | 1 | 1.000 | converged 187, osc 13 |
| bare-anthropic (W5) | 200 | 2 | 0.750 | diverged 108, osc 92 |
| claude-agent-sdk | 200 | 1 | 0.545 | converged 177, div 14, osc 9 |
| crewai | 400 | 2 | 0.750 | converged 184, osc 94, div 122 |
| langchain | 200 | 1 | 1.000 | converged 170, osc 29, div 1 |
| langgraph | 600 | 1 | 0.667 | converged 369, div 143, osc 88 |
| openai-agents | 200 | 1 | 1.000 | converged 199, osc 1 |

- **W5 across {bare, LangGraph, CrewAI}** (matched task/model): stop-iter Kruskal–Wallis **H=0.04, p=0.97**; gain-margin **H=3.00, p=0.22** — the signal behaves **consistently** across adapters on the adversarial workload.
- **W1 LangGraph vs Claude-Agent-SDK** (the documented 5.8 pp winrate-spread cell): stop-iter KW **p=0.30**, near-identical outcome mixes — so the **stop decision** is framework-consistent (which is why the two cells matched on cost savings, 94.7% vs 94.8%). **But the underlying loop dynamics are not:**

  | Robust dynamics metric (W1, identical task/model) | LangGraph | Claude-Agent-SDK |
  |---|---:|---:|
  | trajectories that hit error 0 then **bounce back positive** | **114/200 (57%)** | **66/200 (33%)** |
  | non-monotone (any error rise) | 123/200 | 74/200 |

  LangGraph produces **~1.7× more "found-it-then-broke-it-again" loops** than Claude-Agent-SDK on the same MBPP+ task and the same Haiku-4.5 model. `RESULTS.md` Finding 2 flagged the 5.8 pp judge-winrate spread but stated it had *no evidence to root-cause it* and only speculated about `StateGraph.invoke` prompt-context differences. **This re-analysis gives that open question a concrete, signal-level correlate**: the LangGraph adapter's verify-revise loop is materially more prone to overshooting a correct answer and degrading it. This is correlational, not causal — but it is a measurable behavioral difference in the loop dynamics, which the bench lacked. (The `osc_std` 3.28-vs-0.00 median gap is the same phenomenon viewed in log space.)

### A4 — Early-warning lead time: reproduces the bench's median-2, and the thin margin is premature stops, not late warnings

| Definition | n | median lead | mean | ≥2 iters | ≥3 iters | **lead < 0 (genuinely late)** |
|---|---:|---:|---:|---:|---:|---:|
| **Def 1 — bench catastrophe** (`E_final/E_initial > 2.0`) | 353 | **2.0** | 3.65 | 58.9% | 47.0% | **0.0%** |
| Def 2 — strict first-degrade (≥+1 above running best) | 1,019 | 0.0 | 0.93 | 17.9% | 11.3% | **0.0%** |

- Under the bench's **pre-registered catastrophe definition**, this re-analysis **reproduces `RESULTS.md` exactly: median lead = 2 iterations** (the bench's disclosed missed floor — predicted ≥3 — stands), with the warning firing at-or-before the catastrophe in **353/353** trials.
- Under a **stricter** "first minor degrade" lens, the median lead collapses to **0** — but **72.5% of those are simultaneous (lead = 0)** detections and **0.0% are genuinely late (lead < 0)**. The warning band essentially **never** fires *after* a degrade has already happened.

**Interpretation of the thin-margin / "kill-a-near-success" risk**: the empirical risk is **not** late detection (0% late under both definitions) — it is **premature stopping** (A2: 3.7% of trials stop before the global-best iteration). The signal is, if anything, *eager*: it fires on or before the first degrade. The conservative characterization the data supports is "flags divergence ≥2 iterations before a `max_iter=20` catastrophe"; the residual risk is the 3.7% of loops stopped one step too soon, not warnings that arrive too late.

---

## What the data shows

1. **Cost savings are comparable between stability and confidence.** Both methods sit in the literature's 20–55%-class savings regime, and on this corpus both save ~88–95% vs `max_iter=20`. The stability margin over a matched confidence/plateau baseline is +1.54 pp overall (under the pre-registered +5 pp bar, and within tuning noise — it flips sign with the confidence rule's patience knob). The level signal already captures most of the savings.
2. **Stability catches non-overlapping waste.** Oscillation-band stops uniquely catch **~21% (oscillation) / ~49% (all dynamics)** of the wasted iterations a confidence/plateau rule lets run — the loops where the error keeps intermittently improving so a level signal never trips. This is a pre-registered PASS.
3. **The two signals are complementary.** A rule that combines confidence/plateau (the converge-or-stall majority) with the stability monitor (the oscillation/divergence minority) catches strictly more wasted iterations than either signal alone. This is the pre-registered "valid and valuable" NULL → HYBRID outcome, not a failure.
4. **The dominant engineering lever is the signal class, not the smoothing window** (A1 + A2). The instantaneous `Aβ_smooth` is low-SNR/non-stationary and window-invariant; the move to 0.3.0's trajectory-feature classifier is well-motivated by this data.
5. **The W1 framework-parity spread has a candidate explanation** (A3): the LangGraph adapter overshoots-and-degrades ~1.7× more than the Claude-Agent-SDK adapter on the identical task. Correlational; a follow-up is needed to confirm causality.

---

## Reproducibility & integrity

- `make`-free: `.venv/bin/python analysis/stability_vs_confidence.py` reads only `data/raw/*-registered.jsonl` and `prices.json`; writes `data/results/stability_vs_confidence.json`. **Zero API calls.**
- Pre-registration committed before results (`45c45f1`); the §3–§4 bars were not edited post-hoc. Claim 1 is reported as a NULL against its own bar; the diagnostic refinements (A1 flat/varying split, A3 robust bounce metric, A4 dual definition) were added for *accuracy* and are clearly labeled — none change the two pre-registered verdicts.
- 100% stop-rule replication fidelity is the integrity anchor: the counterfactual is real product behavior, not a reinvention.
- Limitations: per-iteration token cost is modeled (not recorded) via the baseline staircase; the confidence baseline is a verifier-score reduction of confidence-based early-exit (no token-logprob/entropy signal exists in this dataset); single bench epoch; W5 is engineered-adversarial by design (its weight in `W_total` is disclosed).
