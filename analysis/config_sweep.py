"""Free offline config sweep for the LoopGain classifier.

Replays candidate stop policies over the recorded bench trajectories at $0 to
rank classifier configurations *before* spending a paid bench run.

Why it's free: ``max_iter=20`` (B20) ran all 20 iterations for every trial, so
its full error trajectory is "what the loop does if never stopped early." We
replay any stop policy on those trajectories and read off cost (where it stops)
and error-quality (the error it keeps vs B20's last).

What it CAN measure: relative ranking of stop policies on fixed trajectories +
error-based quality (kept_error vs cap-last, false-stop rate).
What it CANNOT: the LLM-judge winrate (needs new judge calls) or live behaviour
(each config would induce different refiner trajectories live). So it's a
SCREEN — only spend a paid bench on a config that clearly wins here.

Usage:
    python analysis/config_sweep.py                # default config grid
    python analysis/config_sweep.py --data data/raw

Add configs in CONFIGS below. See daves-kb/offline-config-sweep for the writeup.
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import statistics
import sys
from pathlib import Path

# Import the shipping classifier from the sibling loopgain-core checkout.
_CORE = Path(__file__).resolve().parents[2] / "loopgain-core"
if _CORE.exists():
    sys.path.insert(0, str(_CORE))
from loopgain.classifier import (  # noqa: E402
    extract_features,
    classify_trajectory,
    DEFAULT_SLOPE_TOL,
    DEFAULT_DIV_MARGIN,
)


def lag1_autocorr(error_history: list[float]) -> float:
    """Lag-1 autocorrelation of the detrended log-residuals (the 'phase' signal)."""
    n = len(error_history)
    if n < 3:
        return 0.0
    log_e = [math.log10(max(e, 1e-12)) for e in error_history]
    xs = list(range(n))
    mx, my = sum(xs) / n, sum(log_e) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    slope = (
        sum((xs[i] - mx) * (log_e[i] - my) for i in range(n)) / sxx if sxx else 0.0
    )
    intc = my - slope * mx
    r = [log_e[i] - (intc + slope * xs[i]) for i in range(n)]
    denom = sum(v * v for v in r)
    return 0.0 if denom < 1e-15 else sum(r[i] * r[i + 1] for i in range(n - 1)) / denom


def classify(hist: list[float], cfg: dict) -> str:
    """Base classifier band + optional config-knob overrides."""
    base = classify_trajectory(hist, target_error=None)  # raw band, no target short-circuit
    f = extract_features(hist)
    n = len(hist)
    if cfg.get("div_aggressive") and base == "STALLING":
        if f.slope_log > 0 and f.e_ratio > 1.5 and f.e_ratio > 1.0 + DEFAULT_DIV_MARGIN:
            return "DIVERGING"
    if cfg.get("osc_autocorr") and base == "STALLING" and n >= 4:
        if lag1_autocorr(hist) < -0.4 and abs(f.slope_log) < DEFAULT_SLOPE_TOL:
            return "OSCILLATING"
    return base


def replay(traj: list[float], target, cfg: dict) -> tuple[int, float]:
    """Replay a stop policy on one full trajectory → (stop_iter, kept_error)."""
    hist: list[float] = []
    prev = None
    for err in traj:
        hist.append(err)
        n = len(hist)
        if target is not None and err <= target:
            return n, min(hist)
        if n < 2:
            prev = "INIT"
            continue
        st = classify(hist, cfg)
        if st == "DIVERGING":
            if cfg.get("div_consec", 1) == 1 or prev == "DIVERGING":
                return n, min(hist)
        elif st == "OSCILLATING":
            if cfg.get("osc_consec", 1) == 1 or prev == "OSCILLATING":
                return n, min(hist)
        elif st == "STALLING":
            if prev == "STALLING":  # 2 consecutive (matches the shipped engine)
                return n, min(hist)
        prev = st
    return len(traj), min(traj)


def load_trials(data_dir: str) -> list[tuple[list[float], float | None]]:
    """Each trial → (B20 full trajectory, target_error). W5 has no target."""
    trials = []
    for path in glob.glob(os.path.join(data_dir, "w*-registered.jsonl")):
        if "judge" in os.path.basename(path):
            continue
        is_w5 = os.path.basename(path).startswith("w5")
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                if rec.get("_header"):
                    continue
                b20 = rec["conditions"].get("B20", {}).get("error_history") or []
                if len(b20) < 20:
                    continue
                trials.append(([float(e) for e in b20], None if is_w5 else 0.0))
    return trials


# Add/modify configs here. Keys: div_consec(1|2), osc_consec(1|2),
# osc_autocorr(bool), div_aggressive(bool).
CONFIGS: dict[str, dict] = {
    "default (current)": dict(div_consec=1),
    "DIVERGING needs 2": dict(div_consec=2),
    "+autocorr OSC": dict(div_consec=1, osc_autocorr=True),
    "divergence aggressive": dict(div_consec=1, div_aggressive=True),
    "2-consec DIV + autocorr": dict(div_consec=2, osc_autocorr=True),
}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", default=str(Path(__file__).resolve().parents[1] / "data" / "raw"))
    args = ap.parse_args(argv)

    trials = load_trials(args.data)
    print(f"trials (full B20 20-iter trajectories): {len(trials)}\n")
    print(f"{'config':<26}{'mean_iters':>11}{'<=cap_qual':>12}{'kept_best':>11}{'false_stop':>12}")
    for name, cfg in CONFIGS.items():
        iters, le_cap, kept_best, missed = [], 0, 0, 0
        for traj, target in trials:
            si, kept = replay(traj, target, cfg)
            iters.append(si)
            if kept <= traj[-1]:
                le_cap += 1
            if kept == min(traj):
                kept_best += 1
            if kept > min(traj):
                missed += 1
        n = len(trials)
        print(
            f"{name:<26}{statistics.mean(iters):>11.2f}"
            f"{100 * le_cap / n:>11.1f}%{100 * kept_best / n:>10.1f}%{100 * missed / n:>11.1f}%"
        )
    print(
        "\nmean_iters = cost proxy (lower=cheaper) | <=cap_qual = % shipped <= max_iter=20's final"
        "\nkept_best = % kept the best the loop reached | false_stop = % a strictly better answer existed later"
        "\nNOTE: relative screen on B20 trajectories — not the judge winrate, not a live re-run."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
