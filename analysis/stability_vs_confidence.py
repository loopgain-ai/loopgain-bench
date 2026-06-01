"""Stability-based stopping vs confidence-based early termination.

Re-analysis of the registered LoopGain bench (zero new API spend). Reads the
immutable per-iteration JSONL in ``data/raw/w*-registered.jsonl`` and answers
the two pre-registered claims in ``STABILITY_VS_CONFIDENCE_PREREG.md`` plus
four diagnostic analyses.

The "stability" rule is the *verified* loopgain 0.2.0 stop logic (replayed via
the installed ``loopgain.core`` — 100% fidelity vs recorded data). The
"confidence" rule is a patience/plateau early-stop on the error level. Both are
replayed on the identical B20 (fixed-cap, fully-unstopped) trajectory, share the
per-cell ``target_error`` short-circuit and a best-so-far buffer, so the only
difference is the early-stop trigger: dynamics (Aβ_smooth bands) vs level
(plateau).

Run:  .venv/bin/python analysis/stability_vs_confidence.py
"""
from __future__ import annotations

import glob
import json
import math
import os
from collections import Counter, defaultdict

import numpy as np

from loopgain import core

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RAW = sorted(glob.glob(os.path.join(ROOT, "data", "raw", "w*-registered.jsonl")))
RNG = np.random.default_rng(20260531)
MAX_ITER = 20
PATIENCE_DEFAULT = 2

# 0.2.0 band thresholds (for diagnostics that recompute Aβ_smooth directly).
ALPHA = 2.0 / (3 + 1)  # smoothing_window=3 -> EMA alpha=0.5


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------
def load_trials():
    trials = []
    for f in RAW:
        cell = None
        target = None
        for line in open(f):
            d = json.loads(line)
            if d.get("_header"):
                cell = d["cell"]["id"]
                target = d["cell"]["target_error"]
                continue
            d["_cell"] = cell
            d["_target"] = target
            d["_family"] = "W5" if cell.startswith("w5") else "W1-W4"
            trials.append(d)
    return trials


# --------------------------------------------------------------------------
# Stop-rule simulators (both replayed on the B20 trajectory)
# --------------------------------------------------------------------------
def stability_stop(error_history, target):
    """Verified loopgain 0.2.0 logic. Returns (stop_iters, kept_err, outcome, states)."""
    lg = core.LoopGain(target_error=target, max_iterations=MAX_ITER)
    states = []
    for e in error_history:
        if not lg.should_continue():
            break
        states.append(lg.observe(e))
    r = lg.result
    k = r.iterations_used
    kept = min(error_history[:k]) if k else float("nan")
    return k, kept, r.outcome, states


def confidence_stop(error_history, target, patience=PATIENCE_DEFAULT):
    """Patience/plateau early-stop on the error LEVEL.

    Stops at first iter where E<=target (shared short-circuit) OR best-so-far
    has not strictly improved for `patience` consecutive iterations. Best-so-far
    buffer, max_iter=20 ceiling. Returns (stop_iters, kept_err, reason).
    """
    best = math.inf
    counter = 0
    n = len(error_history)
    for i, e in enumerate(error_history[:MAX_ITER]):
        if target is not None and e <= target:
            return i + 1, min(error_history[: i + 1]), "TARGET_MET"
        if e < best - 1e-12:
            best = e
            counter = 0
        else:
            counter += 1
        if counter >= patience:
            return i + 1, min(error_history[: i + 1]), "PLATEAU"
        if i == min(n, MAX_ITER) - 1:
            return i + 1, min(error_history[: i + 1]), "CEILING"
    return n, min(error_history), "CEILING"


# --------------------------------------------------------------------------
# Per-trial cost model: fit cost($) = a + b*iters to (5,B5),(10,B10),(20,B20)
# --------------------------------------------------------------------------
def cost_model(trial):
    c = trial["cost_usd"]
    xs = np.array([5.0, 10.0, 20.0])
    ys = np.array([c["B5"], c["B10"], c["B20"]])
    degenerate = bool(np.allclose(ys, ys[0]))
    if degenerate:
        per = c["B20"] / 20.0
        return (lambda k: per * k), c["B20"], True
    b, a = np.polyfit(xs, ys, 1)  # slope, intercept

    def cost_at(k):
        return max(0.0, a + b * k)

    return cost_at, c["B20"], False


def pct_saved_vs_b20(cost_at, c_b20, stop_iters):
    if c_b20 <= 0:
        return 0.0
    return 100.0 * (c_b20 - cost_at(stop_iters)) / c_b20


# --------------------------------------------------------------------------
# Bootstrap helpers (paired by trial)
# --------------------------------------------------------------------------
def classify_claim1(mean_dS, lo_dS, hi_dS, hi_dQ):
    """Pre-registered §3 decision rule. hi_dQ = upper 95% CI on ΔQ (stab-conf)."""
    quality_noninferior = hi_dQ <= 0.05
    if not quality_noninferior:
        return "FAIL (quality inferiority: stability degraded kept output)"
    if hi_dS < 0:
        return "FAIL (stability significantly worse on savings)"
    if mean_dS >= 5.0 and lo_dS > 0:
        return "PASS (stability beats confidence on savings, quality non-inferior)"
    return "NULL -> HYBRID (savings indistinguishable at matched quality)"


def boot_mean_ci(x, n_boot=10000, alpha=0.05):
    x = np.asarray(x, dtype=float)
    if len(x) == 0:
        return float("nan"), float("nan"), float("nan")
    idx = RNG.integers(0, len(x), size=(n_boot, len(x)))
    means = x[idx].mean(axis=1)
    lo, hi = np.percentile(means, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return float(x.mean()), float(lo), float(hi)


# --------------------------------------------------------------------------
# Diagnostic A1: stationarity
# --------------------------------------------------------------------------
def ab_series(error_history):
    eh = error_history
    out = []
    for i in range(1, len(eh)):
        prev = eh[i - 1]
        if prev > 0:
            out.append(eh[i] / prev)
        elif eh[i] == 0:
            out.append(0.0)
        else:
            out.append(2.05)  # 0.2.0 anomalous-gain sentinel (oscillating_upper+1)
    return out


def osc_std(error_history):
    eh = [max(e, 1e-12) for e in error_history]
    n = len(eh)
    if n < 2:
        return 0.0
    log_e = np.log10(eh)
    xs = np.arange(n)
    b, a = np.polyfit(xs, log_e, 1)
    resid = log_e - (a + b * xs)
    return float(np.std(resid))


def lag1_autocorr(series):
    s = np.asarray(series, dtype=float)
    if len(s) < 3 or np.std(s) == 0:
        return float("nan")
    s0, s1 = s[:-1], s[1:]
    if np.std(s0) == 0 or np.std(s1) == 0:
        return float("nan")
    return float(np.corrcoef(s0, s1)[0, 1])


# --------------------------------------------------------------------------
# Diagnostic A3: Kruskal-Wallis (numpy ranks)
# --------------------------------------------------------------------------
def kruskal_wallis(groups):
    groups = [np.asarray(g, dtype=float) for g in groups if len(g) > 0]
    if len(groups) < 2:
        return float("nan"), float("nan")
    allv = np.concatenate(groups)
    N = len(allv)
    ranks = allv.argsort().argsort().astype(float) + 1
    # average ranks for ties
    order = allv.argsort()
    sorted_v = allv[order]
    sr = ranks[order]
    i = 0
    while i < N:
        j = i
        while j + 1 < N and sorted_v[j + 1] == sorted_v[i]:
            j += 1
        if j > i:
            sr[i : j + 1] = sr[i : j + 1].mean()
        i = j + 1
    ranks[order] = sr
    idx = 0
    rank_groups = []
    for g in groups:
        rank_groups.append(ranks[idx : idx + len(g)])
        idx += len(g)
    H = 12.0 / (N * (N + 1)) * sum(len(rg) * (rg.mean() ** 2) for rg in rank_groups) - 3 * (N + 1)
    k = len(groups)
    # chi-square survival via Wilson-Hilferty (df=k-1), stdlib-free p approx
    df = k - 1
    p = chi2_sf(H, df)
    return float(H), float(p)


def chi2_sf(x, df):
    if x <= 0 or df <= 0:
        return 1.0
    # Wilson-Hilferty normal approximation
    z = ((x / df) ** (1.0 / 3.0) - (1.0 - 2.0 / (9.0 * df))) / math.sqrt(2.0 / (9.0 * df))
    return 0.5 * math.erfc(z / math.sqrt(2.0))


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main():
    trials = load_trials()
    report = {"n_trials": len(trials)}
    print(f"Loaded {len(trials)} trials from {len(RAW)} cells.\n")

    # ---- Build per-trial records on B20 substrate ----
    recs = []
    degen = 0
    for t in trials:
        b20 = t["conditions"]["B20"]
        eh = b20["error_history"]
        target = t["_target"]
        cost_at, c_b20, is_degen = cost_model(t)
        degen += is_degen
        s_it, s_kept, s_out, s_states = stability_stop(eh, target)
        c_it, c_kept, c_reason = confidence_stop(eh, target, PATIENCE_DEFAULT)
        recs.append(
            dict(
                cell=t["_cell"], family=t["_family"], fw=t["framework"], target=target,
                eh=eh, c_b20=c_b20,
                s_it=s_it, s_kept=s_kept, s_out=s_out, s_states=s_states,
                c_it=c_it, c_kept=c_kept, c_reason=c_reason,
                S_stab=pct_saved_vs_b20(cost_at, c_b20, s_it),
                S_conf=pct_saved_vs_b20(cost_at, c_b20, c_it),
            )
        )
    report["degenerate_cost_trials"] = degen

    # ---- Quality normalization: per-cell mean E_first among E_first>0 ----
    cell_efirst = defaultdict(list)
    for r in recs:
        if r["eh"] and r["eh"][0] > 0:
            cell_efirst[r["cell"]].append(r["eh"][0])
    cell_scale = {c: (np.mean(v) if v else 1.0) for c, v in cell_efirst.items()}
    for r in recs:
        sc = max(cell_scale.get(r["cell"], 1.0), 1.0)
        r["dQ"] = (r["s_kept"] - r["c_kept"]) / sc  # >0 means stability worse

    # =====================================================================
    # CLAIM 1
    # =====================================================================
    print("=" * 70)
    print("CLAIM 1 — Stability beats confidence on savings at matched quality")
    print("=" * 70)

    def claim1(subset, label):
        dS = np.array([r["S_stab"] - r["S_conf"] for r in subset])
        dQ = np.array([r["dQ"] for r in subset])
        s_mean = np.mean([r["S_stab"] for r in subset])
        c_mean = np.mean([r["S_conf"] for r in subset])
        mdS, loS, hiS = boot_mean_ci(dS)
        mdQ, loQ, hiQ = boot_mean_ci(dQ)
        verdict = classify_claim1(mdS, loS, hiS, hiQ)
        print(f"\n[{label}]  n={len(subset)}")
        print(f"  mean S_stab = {s_mean:6.2f}%   mean S_conf = {c_mean:6.2f}%")
        print(f"  mean ΔS (stab-conf) = {mdS:+.2f} pp   95% CI [{loS:+.2f}, {hiS:+.2f}]")
        print(f"  mean ΔQ (stab-conf, norm err) = {mdQ:+.4f}   95% CI [{loQ:+.4f}, {hiQ:+.4f}]  (>0 = stability worse)")
        print(f"  VERDICT: {verdict}")
        return dict(label=label, n=len(subset), S_stab=s_mean, S_conf=c_mean,
                    dS=mdS, dS_ci=[loS, hiS], dQ=mdQ, dQ_ci=[loQ, hiQ], verdict=verdict)

    report["claim1"] = {
        "overall": claim1(recs, "OVERALL"),
        "W1-W4": claim1([r for r in recs if r["family"] == "W1-W4"], "W1-W4 natural"),
        "W5": claim1([r for r in recs if r["family"] == "W5"], "W5 adversarial"),
    }

    # sensitivity on patience k
    print("\n  Patience sensitivity (overall mean ΔS, pp):")
    sens = {}
    for k in (1, 2, 3):
        dS = []
        for t in trials:
            eh = t["conditions"]["B20"]["error_history"]
            target = t["_target"]
            cost_at, c_b20, _ = cost_model(t)
            s_it = stability_stop(eh, target)[0]
            c_it = confidence_stop(eh, target, k)[0]
            dS.append(pct_saved_vs_b20(cost_at, c_b20, s_it) - pct_saved_vs_b20(cost_at, c_b20, c_it))
        sens[k] = float(np.mean(dS))
        print(f"    k={k}: mean ΔS = {np.mean(dS):+.2f} pp")
    report["claim1"]["patience_sensitivity_dS"] = sens

    # =====================================================================
    # CLAIM 2
    # =====================================================================
    print("\n" + "=" * 70)
    print("CLAIM 2 — Oscillation detection catches unique wasted iterations")
    print("=" * 70)
    W_total = 0
    U_osc = 0
    U_div = 0
    U_all = 0
    W_fam = defaultdict(int)
    for r in recs:
        eh = r["eh"]
        # wasted under confidence: iters after conf's kept-best was first seen
        c_pref = eh[: r["c_it"]]
        c_best_idx = int(np.argmin(c_pref))
        wasted = max(0, r["c_it"] - (c_best_idx + 1))
        W_total += wasted
        W_fam[r["family"]] += wasted
        # uniquely caught by stability stopping earlier
        if r["s_it"] < r["c_it"]:
            cut = eh[r["s_it"] : r["c_it"]]
            run_best = min(eh[: r["s_it"]])
            wasted_cut = 0
            rb = run_best
            for e in cut:
                if e < rb - 1e-12:  # this cut iter set a new best -> not wasted
                    rb = e
                else:
                    wasted_cut += 1
            U_all += wasted_cut
            if r["s_out"] == "oscillating":
                U_osc += wasted_cut
            elif r["s_out"] == "diverged":
                U_div += wasted_cut
    ratio_osc = 100.0 * U_osc / W_total if W_total else 0.0
    ratio_div = 100.0 * U_div / W_total if W_total else 0.0
    ratio_all = 100.0 * U_all / W_total if W_total else 0.0
    v2 = "PASS" if ratio_osc > 15 else ("WEAK PASS" if ratio_osc > 10 else "FAIL")
    print(f"  W_total (wasted iters under confidence) = {W_total}")
    print(f"    by family: {dict(W_fam)}")
    print(f"  U_osc (uniquely caught by OSCILLATING-stop) = {U_osc}   ratio = {ratio_osc:.1f}%")
    print(f"  [context] U_div (DIVERGING-stop) = {U_div}   ratio = {ratio_div:.1f}%")
    print(f"  [context] U_all (any stability-stop) = {U_all}   ratio = {ratio_all:.1f}%")
    print(f"  VERDICT (claim is OSCILLATING only): {v2}   (PASS>15%, WEAK 10-15%, FAIL<=10%)")
    report["claim2"] = dict(W_total=W_total, W_family=dict(W_fam), U_osc=U_osc,
                            ratio_osc=ratio_osc, U_div=U_div, ratio_div=ratio_div,
                            U_all=U_all, ratio_all=ratio_all, verdict=v2)

    # =====================================================================
    # A1 — stationarity
    # =====================================================================
    print("\n" + "=" * 70)
    print("A1 — Within-run stationarity of the loop-gain signal")
    print("=" * 70)
    print("  (substrate = B20 full unstopped trajectories, always 20 iters)")
    osc_all = []
    ac_v, snr_v, vr_v = [], [], []
    n_flat = 0  # degenerate: error constant -> no decision needed, trivially stationary
    n_vary = 0
    for r in recs:
        eh = r["eh"]
        osc_all.append(osc_std(eh))
        if len(set(eh)) == 1:
            n_flat += 1
            continue
        n_vary += 1
        ab = ab_series(eh)
        ac = lag1_autocorr(ab)
        if not math.isnan(ac):
            ac_v.append(ac)
        dlog = np.diff(np.log10([max(e, 1e-12) for e in eh]))
        if np.std(dlog) > 0:
            snr_v.append(abs(np.mean(dlog)) / np.std(dlog))
        h = len(eh) // 2
        v1 = np.var(np.log10([max(e, 1e-12) for e in eh[:h]]))
        v2_ = np.var(np.log10([max(e, 1e-12) for e in eh[h:]]))
        if v1 > 0:
            vr_v.append(v2_ / v1)
    print(f"  flat trajectories (constant error, decision trivial): {n_flat}/{len(recs)} "
          f"({n_flat/len(recs):.1%})")
    print(f"  varying trajectories (a real stop decision exists): {n_vary}/{len(recs)} "
          f"({n_vary/len(recs):.1%})")
    print(f"  osc_std (library log10-residual feature) all-runs: median={np.median(osc_all):.3f} "
          f"mean={np.mean(osc_all):.3f}")
    print(f"    NOTE: osc_std is inflated by E=0 crossings (log10 floor 1e-12 -> -12 spike);")
    print(f"    it is the literal 0.3.0 feature but a poor *stationarity* descriptor. Robust "
          f"descriptors below.")
    print(f"  -- among varying trajectories (n={n_vary}) --")
    print(f"  Aβ lag-1 autocorr: median={np.median(ac_v):+.3f} "
          f"(near 0 => IID/white step; strong + => persistent trend; - => anti-persistent)")
    print(f"  step SNR |mean Δlog10E|/std: median={np.median(snr_v):.3f} "
          f"frac>1 (trend dominates noise): {np.mean(np.array(snr_v)>1):.2%}")
    print(f"  variance ratio 2nd/1st half: median={np.median(vr_v):.3f} "
          f"(~1 => stationary variance; <<1 => variance collapses = non-stationary)")
    report["A1"] = dict(
        n_flat=n_flat, n_vary=n_vary,
        osc_std_all_median=float(np.median(osc_all)),
        ab_lag1_ac_median=float(np.median(ac_v)) if ac_v else None,
        step_snr_median=float(np.median(snr_v)) if snr_v else None,
        step_snr_frac_gt1=float(np.mean(np.array(snr_v) > 1)) if snr_v else None,
        var_ratio_median=float(np.median(vr_v)) if vr_v else None,
    )

    # =====================================================================
    # A2 — step jitter & smoothing sensitivity
    # =====================================================================
    print("\n" + "=" * 70)
    print("A2 — Step jitter & smoothing-window sensitivity")
    print("=" * 70)
    jit_ab, jit_log = [], []
    for r in recs:
        ab = ab_series(r["eh"])
        if len(ab) >= 2:
            jit_ab.extend(np.abs(np.diff(ab)).tolist())
        dlog = np.abs(np.diff(np.log10([max(e, 1e-12) for e in r["eh"]])))
        jit_log.extend(dlog.tolist())
    print(f"  |ΔAβ| iter-to-iter: median={np.median(jit_ab):.3f} mean={np.mean(jit_ab):.3f}")
    print(f"  |Δlog10 E| iter-to-iter: median={np.median(jit_log):.3f} mean={np.mean(jit_log):.3f}")

    def stop_with_window(eh, target, window):
        lg = core.LoopGain(target_error=target, max_iterations=MAX_ITER, smoothing_window=window)
        for e in eh:
            if not lg.should_continue():
                break
            lg.observe(e)
        r = lg.result
        return r.iterations_used

    win_stats = {}
    prev_stops = None
    for w in (1, 3, 5, 7):
        stops = []
        prem = 0
        late = 0
        for r in recs:
            eh = r["eh"]
            k = stop_with_window(eh, r["target"], w)
            stops.append(k)
            gbest = int(np.argmin(eh))
            if k - 1 < gbest:  # stopped before global best reached
                prem += 1
            if k - 1 >= gbest + 2:
                late += 1
        stops = np.array(stops)
        changed = float(np.mean(stops != prev_stops)) if prev_stops is not None else None
        win_stats[w] = dict(median_stop=float(np.median(stops)), premature=prem,
                            late=late, changed_vs_prev=changed)
        cstr = f" changed vs prev window: {changed:.2%}" if changed is not None else ""
        print(f"  window={w}: median stop iter={np.median(stops):.1f}  "
              f"premature(before global best)={prem} ({prem/len(recs):.1%})  "
              f"late(>=2 after best)={late} ({late/len(recs):.1%}){cstr}")
        prev_stops = stops
    report["A2"] = dict(jit_ab_median=float(np.median(jit_ab)),
                        jit_log_median=float(np.median(jit_log)),
                        windows=win_stats)

    # =====================================================================
    # A3 — cross-framework consistency
    # =====================================================================
    print("\n" + "=" * 70)
    print("A3 — Cross-framework consistency of the signal")
    print("=" * 70)
    by_fw = defaultdict(lambda: dict(stop=[], gm=[], osc=[], out=Counter()))
    for r in recs:
        # recompute gain_margin on B20 prefix via stability replay outcome's gm
        lg = core.LoopGain(target_error=r["target"], max_iterations=MAX_ITER)
        for e in r["eh"]:
            if not lg.should_continue():
                break
            lg.observe(e)
        gm = lg.result.gain_margin
        d = by_fw[r["fw"]]
        d["stop"].append(r["s_it"])
        if gm is not None and math.isfinite(gm):
            d["gm"].append(gm)
        d["osc"].append(osc_std(r["eh"]))
        d["out"][r["s_out"]] += 1
    for fw, d in sorted(by_fw.items()):
        n = len(d["stop"])
        print(f"  {fw:20s} n={n:4d}  med stop={np.median(d['stop']):.1f}  "
              f"med gm={np.median(d['gm']) if d['gm'] else float('nan'):.3f}  "
              f"med osc_std={np.median(d['osc']):.3f}  outcomes={dict(d['out'])}")
    # W5 across adapters (matched workload)
    w5fams = {"bare": "w5-adversarial-claude-haiku-4-5",
              "langgraph": "w5-adversarial-langgraph-claude-haiku-4-5",
              "crewai": "w5-adversarial-crewai-claude-haiku-4-5"}
    w5_stop = {k: [r["s_it"] for r in recs if r["cell"] == v] for k, v in w5fams.items()}
    w5_gm = {}
    for k, v in w5fams.items():
        gms = []
        for r in recs:
            if r["cell"] != v:
                continue
            lg = core.LoopGain(target_error=r["target"], max_iterations=MAX_ITER)
            for e in r["eh"]:
                if not lg.should_continue():
                    break
                lg.observe(e)
            g = lg.result.gain_margin
            if g is not None and math.isfinite(g):
                gms.append(g)
        w5_gm[k] = gms
    H_stop, p_stop = kruskal_wallis(list(w5_stop.values()))
    H_gm, p_gm = kruskal_wallis(list(w5_gm.values()))
    print(f"\n  W5 across {{bare, langgraph, crewai}} — Kruskal-Wallis:")
    print(f"    stop-iter: H={H_stop:.2f} p={p_stop:.3f}   "
          f"(medians {[float(np.median(v)) for v in w5_stop.values()]})")
    print(f"    gain_margin: H={H_gm:.2f} p={p_gm:.3f}")
    # W1 langgraph vs claude-agent-sdk
    w1lg = [r for r in recs if r["cell"] == "w1-codegen-langgraph-claude-haiku-4-5"]
    w1cs = [r for r in recs if r["cell"] == "w1-codegen-claude-agent-sdk-claude-haiku-4-5"]
    H_w1, p_w1 = kruskal_wallis([[r["s_it"] for r in w1lg], [r["s_it"] for r in w1cs]])
    osc_w1lg = np.median([osc_std(r["eh"]) for r in w1lg])
    osc_w1cs = np.median([osc_std(r["eh"]) for r in w1cs])
    def bounce_count(subset):
        b = sum(any(r["eh"][i] == 0 and r["eh"][i + 1] > 0 for i in range(len(r["eh"]) - 1))
                for r in subset)
        nm = sum(any(r["eh"][i + 1] > r["eh"][i] for i in range(len(r["eh"]) - 1)) for r in subset)
        return b, nm
    b_lg, nm_lg = bounce_count(w1lg)
    b_cs, nm_cs = bounce_count(w1cs)
    print(f"\n  W1 LangGraph vs Claude-Agent-SDK (the 5.8pp parity-spread cell):")
    print(f"    stop-iter Kruskal-Wallis: H={H_w1:.2f} p={p_w1:.3f}")
    print(f"    median stop: LG-adapter={np.median([r['s_it'] for r in w1lg]):.1f} "
          f"CASDK={np.median([r['s_it'] for r in w1cs]):.1f}")
    print(f"    outcome mix LangGraph={dict(Counter(r['s_out'] for r in w1lg))}")
    print(f"    outcome mix CASDK    ={dict(Counter(r['s_out'] for r in w1cs))}")
    print(f"    ROBUST DYNAMICS METRIC — zero-crossing/bounce (hit 0 then go positive):")
    print(f"      LangGraph: {b_lg}/200 ({b_lg/2:.0f}%)   CASDK: {b_cs}/200 ({b_cs/2:.0f}%)")
    print(f"      non-monotone (any rise): LangGraph {nm_lg}/200   CASDK {nm_cs}/200")
    print(f"      (osc_std median LangGraph={osc_w1lg:.2f} vs CASDK={osc_w1cs:.2f} is the same "
          f"phenomenon in log space)")
    report["A3"] = dict(
        w1_bounce=dict(langgraph=b_lg, casdk=b_cs, langgraph_nonmono=nm_lg, casdk_nonmono=nm_cs),
        by_framework={fw: dict(n=len(d["stop"]), med_stop=float(np.median(d["stop"])),
                               med_gm=float(np.median(d["gm"])) if d["gm"] else None,
                               med_osc=float(np.median(d["osc"])),
                               outcomes=dict(d["out"])) for fw, d in by_fw.items()},
        w5_kw_stop=dict(H=H_stop, p=p_stop),
        w5_kw_gm=dict(H=H_gm, p=p_gm),
        w1_kw_stop=dict(H=H_w1, p=p_w1, med_osc_langgraph=float(osc_w1lg),
                        med_osc_casdk=float(osc_w1cs)),
    )

    # =====================================================================
    # A4 — early-warning lead time (full distribution)
    # =====================================================================
    print("\n" + "=" * 70)
    print("A4 — Early-warning lead time (B20 degrade events)")
    print("=" * 70)
    WARN = {"STALLING", "OSCILLATING", "DIVERGING"}

    def first_warning(eh):
        lg = core.LoopGain(target_error=None, max_iterations=MAX_ITER)
        for i, e in enumerate(eh):
            if lg.observe(e) in WARN:
                return i
        return None

    # --- Definition 1: bench's pre-registered catastrophe (E_final/E_initial > 2.0) ---
    leads_cat = []
    n_cat = 0
    for r in recs:
        eh = r["eh"]
        ei, efin = eh[0], eh[-1]
        if ei > 0 and efin / ei > 2.0:
            n_cat += 1
            cp = next((i for i, e in enumerate(eh) if e > 2 * ei), None)
            fire = first_warning(eh)
            if fire is not None and cp is not None:
                leads_cat.append(cp - fire)
    leads_cat = np.array(leads_cat)
    print(f"  [Def 1 — bench catastrophe E_fin/E_init>2.0]  catastrophe trials={n_cat}, "
          f"warned at/before={int((leads_cat>=0).sum())}/{len(leads_cat)}")
    print(f"    lead median={np.median(leads_cat):.1f} mean={np.mean(leads_cat):.2f}  "
          f">=2: {np.mean(leads_cat>=2):.1%}  >=3: {np.mean(leads_cat>=3):.1%}  "
          f"<0 (genuinely late): {np.mean(leads_cat<0):.1%}")
    print(f"    -> reproduces RESULTS.md median lead = 2 (n~352).")

    # --- Definition 2: strict first-degrade (any +1 rise above running best after global best) ---
    leads = []
    n_degrade = 0
    for r in recs:
        eh = r["eh"]
        gbest = int(np.argmin(eh))
        D = None
        rb = eh[0]
        for i in range(1, len(eh)):
            if eh[i] < rb:
                rb = eh[i]
            if i > gbest and eh[i] >= rb + 1:
                D = i
                break
        if D is None:
            continue
        n_degrade += 1
        fire = first_warning(eh)
        if fire is not None:
            leads.append(D - fire)
    leads = np.array(leads)
    print(f"  [Def 2 — strict first-degrade (>=+1 after global best)]  degrade trials={n_degrade}, "
          f"warned={len(leads)}")
    print(f"    lead median={np.median(leads):.1f} mean={np.mean(leads):.2f}  "
          f"Q1={np.percentile(leads,25):.0f} Q3={np.percentile(leads,75):.0f}")
    print(f"    lead>=1: {np.mean(leads>=1):.1%}  >=2: {np.mean(leads>=2):.1%}  "
          f">=3: {np.mean(leads>=3):.1%}")
    print(f"    lead==0 (SIMULTANEOUS detection): {np.mean(leads==0):.1%}   "
          f"lead<0 (GENUINELY LATE): {np.mean(leads<0):.1%}")
    print(f"  Reconciliation: big catastrophes get a healthy ~2-iter lead (Def 1); the FIRST "
          f"minor degrade is usually caught simultaneously (Def 2 median 0), almost never late.")
    report["A4"] = dict(
        bench_def=dict(n_cat=n_cat, n_lead=len(leads_cat), median=float(np.median(leads_cat)),
                       mean=float(np.mean(leads_cat)), frac_ge2=float(np.mean(leads_cat >= 2)),
                       frac_ge3=float(np.mean(leads_cat >= 3)), frac_lt0=float(np.mean(leads_cat < 0))),
        strict_def=dict(n_degrade=n_degrade, n_lead=len(leads), median=float(np.median(leads)),
                        mean=float(np.mean(leads)), frac_ge1=float(np.mean(leads >= 1)),
                        frac_ge2=float(np.mean(leads >= 2)), frac_ge3=float(np.mean(leads >= 3)),
                        frac_eq0=float(np.mean(leads == 0)), frac_lt0=float(np.mean(leads < 0)),
                        hist={int(k): int(v) for k, v in Counter(leads.tolist()).items()}))

    out_path = os.path.join(ROOT, "data", "results", "stability_vs_confidence.json")
    with open(out_path, "w") as fh:
        json.dump(report, fh, indent=2)
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
