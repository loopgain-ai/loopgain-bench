"""Generate the six bench charts for RESULTS.md.

Reads from data/raw/*-registered.jsonl and data/raw/judge-*-registered.jsonl,
writes PNGs to data/results/charts/. Run via `python -m analysis.charts`.

Charts produced:
  1. cost_by_condition.png    — total spend per condition across all cells
  2. winrate_with_ci.png      — per-cell judge winrate with 95% bootstrap CIs
  3. savings_by_segment.png   — LG savings within each LG-outcome segment
  4. band_emissions.png       — band emission counts (highlights CONVERGING/STALLING)
  5. lead_time_histogram.png  — for B20-divergent trials, LG first-warn-iter
  6. hero_seed34.png          — error-vs-iter for the DM screenshot trial
"""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

RAW = Path(__file__).resolve().parents[1] / "data" / "raw"
OUT = Path(__file__).resolve().parents[1] / "data" / "results" / "charts"
OUT.mkdir(parents=True, exist_ok=True)

# Brand colors — orange for LG, neutral grays for baselines
LG_COLOR = "#E07B00"
BASELINE_COLORS = {"B5": "#B8B8B8", "B10": "#808080", "B20": "#404040"}


def load_registered_trials() -> list[dict]:
    trials: list[dict] = []
    for p in sorted(RAW.glob("*-registered.jsonl")):
        if p.name.startswith("judge-"):
            continue
        for line in p.read_text().strip().split("\n")[1:]:
            rec = json.loads(line)
            if rec.get("_trial_error") or rec.get("_header"):
                continue
            trials.append(rec)
    return trials


def load_judge_verdicts() -> dict[str, str]:
    """trial_id -> 'LG' | 'TIE' | 'B20'"""
    out: dict[str, str] = {}
    for p in sorted(RAW.glob("judge-*-registered.jsonl")):
        with p.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                if rec.get("_header"):
                    continue
                tid = rec.get("trial_id")
                lg_pos = rec.get("lg_position")
                choice = rec.get("judge_choice")
                if not tid:
                    continue
                if choice == "TIE":
                    out[tid] = "TIE"
                elif (choice == "A" and lg_pos == "A") or (choice == "B" and lg_pos == "B"):
                    out[tid] = "LG"
                elif choice in ("A", "B"):
                    out[tid] = "B20"
    return out


def short_cell_name(wid: str) -> str:
    """Render workload id compactly for chart labels."""
    return (
        wid.replace("claude-haiku-4-5", "Hk")
        .replace("claude-sonnet-4-6", "So")
        .replace("gpt-4-1-mini", "GPT")
        .replace("w1-codegen-", "W1·")
        .replace("w2-debate-", "W2·")
        .replace("w3-planner-", "W3·")
        .replace("w4-rag-", "W4·")
        .replace("w5-adversarial-", "W5·")
        .replace("claude-agent-sdk", "CASDK")
        .replace("-Hk", " Hk")
        .replace("-So", " So")
        .replace("-GPT", " GPT")
    )


def chart_1_cost_by_condition(trials: list[dict]) -> None:
    totals = {"B5": 0.0, "B10": 0.0, "B20": 0.0, "LG": 0.0}
    for t in trials:
        for c in totals:
            totals[c] += t["cost_usd"][c]

    fig, ax = plt.subplots(figsize=(7, 4.5))
    conds = ["B5", "B10", "B20", "LG"]
    values = [totals[c] for c in conds]
    colors = [BASELINE_COLORS["B5"], BASELINE_COLORS["B10"], BASELINE_COLORS["B20"], LG_COLOR]
    bars = ax.bar(conds, values, color=colors)
    for bar, v in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2, v + max(values) * 0.01,
                f"${v:.2f}", ha="center", fontsize=11, fontweight="bold")
    ax.set_ylabel("Total API spend (USD)", fontsize=11)
    ax.set_title(f"Total API spend across all 10 cells, n=200 each "
                 f"(LoopGain saves {(1 - totals['LG'] / totals['B20']) * 100:.1f}% vs max_iter=20)",
                 fontsize=11)
    ax.grid(axis="y", alpha=0.3)
    ax.set_ylim(0, max(values) * 1.15)
    plt.tight_layout()
    plt.savefig(OUT / "cost_by_condition.png", dpi=140)
    plt.close()


def _bootstrap_ci(scores: list[float], *, n_resamples: int = 5000, seed: int = 0) -> tuple[float, float, float]:
    arr = np.asarray(scores, dtype=float)
    if arr.size == 0:
        return (float("nan"),) * 3
    mean = float(arr.mean())
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, arr.size, size=(n_resamples, arr.size))
    means = arr[idx].mean(axis=1)
    return mean, float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


def chart_2_winrate_with_ci(trials: list[dict], verdicts: dict[str, str]) -> None:
    by_cell: dict[str, list[float]] = defaultdict(list)
    for t in trials:
        v = verdicts.get(t["trial_id"])
        if v == "LG":
            by_cell[t["workload"]].append(1.0)
        elif v == "TIE":
            by_cell[t["workload"]].append(0.5)
        elif v == "B20":
            by_cell[t["workload"]].append(0.0)

    cells = sorted(by_cell.keys())
    means, lo_err, hi_err = [], [], []
    for wid in cells:
        m, lo, hi = _bootstrap_ci(by_cell[wid])
        means.append(m)
        lo_err.append(m - lo)
        hi_err.append(hi - m)

    fig, ax = plt.subplots(figsize=(11, 5))
    labels = [short_cell_name(c) for c in cells]
    x = np.arange(len(cells))
    colors = [LG_COLOR if m >= 0.5 else "#999" for m in means]
    ax.bar(x, means, yerr=[lo_err, hi_err], color=colors, capsize=4)
    ax.axhline(0.5, color="black", linestyle="--", alpha=0.5, label="Null (parity)")
    ax.axhline(0.40, color="red", linestyle=":", alpha=0.6, label="Kill threshold (winrate < 0.40)")
    ax.set_ylabel("LG winrate (LG-wins + 0.5·ties) / n", fontsize=11)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=9)
    ax.set_ylim(0, 1.0)
    ax.set_title("Per-cell judge winrate (LG vs B20) with 95% bootstrap CI, n=200", fontsize=11)
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    plt.savefig(OUT / "winrate_with_ci.png", dpi=140)
    plt.close()


def chart_3_savings_by_segment(trials: list[dict]) -> None:
    by_outcome: dict[str, list[dict]] = defaultdict(list)
    for t in trials:
        by_outcome[t["conditions"]["LG"]["outcome"]].append(t)

    segments = ["converged", "oscillating", "diverged"]
    width = 0.25
    x = np.arange(len(segments))

    save_vs_b5, save_vs_b10, save_vs_b20 = [], [], []
    ns = []
    for s in segments:
        ts = by_outcome.get(s, [])
        ns.append(len(ts))
        if not ts:
            save_vs_b5.append(0); save_vs_b10.append(0); save_vs_b20.append(0)
            continue
        med_b5 = statistics.median(t["cost_usd"]["B5"] for t in ts)
        med_b10 = statistics.median(t["cost_usd"]["B10"] for t in ts)
        med_b20 = statistics.median(t["cost_usd"]["B20"] for t in ts)
        med_lg = statistics.median(t["cost_usd"]["LG"] for t in ts)
        save_vs_b5.append((1 - med_lg / med_b5) * 100 if med_b5 else 0)
        save_vs_b10.append((1 - med_lg / med_b10) * 100 if med_b10 else 0)
        save_vs_b20.append((1 - med_lg / med_b20) * 100 if med_b20 else 0)

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.bar(x - width, save_vs_b5, width, label="vs B5 (max_iter=5)", color=BASELINE_COLORS["B5"])
    ax.bar(x, save_vs_b10, width, label="vs B10 (max_iter=10)", color=BASELINE_COLORS["B10"])
    ax.bar(x + width, save_vs_b20, width, label="vs B20 (max_iter=20)", color=BASELINE_COLORS["B20"])

    for i, n in enumerate(ns):
        ax.text(i, 102, f"n={n}", ha="center", fontsize=10)

    ax.set_ylabel("LoopGain median-cost savings (%)", fontsize=11)
    ax.set_xticks(x)
    ax.set_xticklabels([f"{s.upper()}" for s in segments], fontsize=11)
    ax.set_title("LG cost savings within each LG terminal-outcome segment "
                 "(median over trials in segment)", fontsize=11)
    ax.legend(loc="lower right", fontsize=10)
    ax.grid(axis="y", alpha=0.3)
    ax.set_ylim(0, 110)
    ax.axhline(0, color="black", linewidth=0.6)
    plt.tight_layout()
    plt.savefig(OUT / "savings_by_segment.png", dpi=140)
    plt.close()


def chart_4_band_emissions(trials: list[dict]) -> None:
    states: Counter = Counter()
    for t in trials:
        for s in t["conditions"]["LG"].get("state_history", []):
            states[s] += 1

    order = ["TARGET_MET", "FAST_CONVERGE", "CONVERGING", "STALLING", "OSCILLATING", "DIVERGING"]
    values = [states.get(b, 0) for b in order]
    fig, ax = plt.subplots(figsize=(9, 4.5))
    color_map = {
        "TARGET_MET": "#5DA5DA",
        "FAST_CONVERGE": "#60BD68",
        "CONVERGING": "#FAA43A",
        "STALLING": "#F17CB0",
        "OSCILLATING": "#B276B2",
        "DIVERGING": "#F15854",
    }
    bars = ax.bar(order, values, color=[color_map[b] for b in order])
    for bar, v in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2, v + max(values) * 0.01,
                f"{v}", ha="center", fontsize=10, fontweight="bold")
    ax.set_ylabel("Total band emissions across 2,000 trials", fontsize=11)
    ax.set_title("LoopGain band emission distribution "
                 "(CONVERGING and STALLING bands sparsely exercised in this bench)",
                 fontsize=10.5)
    ax.grid(axis="y", alpha=0.3)
    plt.xticks(rotation=15, ha="right")
    plt.tight_layout()
    plt.savefig(OUT / "band_emissions.png", dpi=140)
    plt.close()


def chart_5_lead_time_histogram(trials: list[dict]) -> None:
    leads: list[int] = []
    warn_bands = {"STALLING", "OSCILLATING", "DIVERGING"}
    for t in trials:
        b20 = t["conditions"]["B20"]
        eh = b20.get("error_history", [])
        if not eh or eh[0] <= 0:
            continue
        ratio_final = eh[-1] / eh[0]
        if ratio_final <= 2.0:
            continue
        sh = t["conditions"]["LG"].get("state_history", [])
        first_warn = next((i + 1 for i, s in enumerate(sh) if s in warn_bands), None)
        catastrophe = next((i + 1 for i, e in enumerate(eh) if e / eh[0] > 2.0), len(eh))
        if first_warn is None:
            continue
        leads.append(catastrophe - first_warn)

    fig, ax = plt.subplots(figsize=(8, 4.5))
    if leads:
        bins = np.arange(min(leads) - 0.5, max(leads) + 1.5, 1)
        ax.hist(leads, bins=bins, color=LG_COLOR, edgecolor="black")
        median = int(statistics.median(leads))
        ax.axvline(median, color="black", linestyle="--", label=f"Median lead = {median} iters")
        ax.legend(loc="upper right", fontsize=10)
        ax.set_xlabel("Iterations LG warned BEFORE B20 catastrophe (E_final/E_initial > 2.0)", fontsize=10)
        ax.set_ylabel("Count of trials", fontsize=11)
        ax.set_title(f"Early-warning lead time: LG flags STALLING/OSCILLATING/DIVERGING "
                     f"before B20 diverges (n={len(leads)} trials)",
                     fontsize=10.5)
    else:
        ax.text(0.5, 0.5, "No catastrophe trials at this threshold", ha="center", va="center",
                transform=ax.transAxes, fontsize=12)
        ax.set_axis_off()
    ax.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    plt.savefig(OUT / "lead_time_histogram.png", dpi=140)
    plt.close()


def chart_6_hero_seed34(trials: list[dict]) -> None:
    hero = next(t for t in trials if t["trial_id"] == "w1-codegen-langgraph-claude-haiku-4-5-seed34")
    lg_eh = hero["conditions"]["LG"]["error_history"]
    b20_eh = hero["conditions"]["B20"]["error_history"]

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(range(1, len(b20_eh) + 1), b20_eh, marker="o", color=BASELINE_COLORS["B20"],
            label=f"B20 (max_iter=20) — final error = {b20_eh[-1]:.0f}, broken at iter 20")
    ax.plot(range(1, len(lg_eh) + 1), lg_eh, marker="o", color=LG_COLOR, linewidth=2.5,
            markersize=10, label=f"LoopGain — converged at iter 2, kept correct output")
    # Mark LG stop
    ax.scatter([len(lg_eh)], [lg_eh[-1]], s=200, facecolors="none", edgecolors=LG_COLOR,
               linewidths=2.5, zorder=5)
    ax.text(len(lg_eh) + 0.3, lg_eh[-1] + 0.5, "LG stops\n(TARGET_MET)", fontsize=10, color=LG_COLOR)

    ax.set_xlabel("Iteration", fontsize=11)
    ax.set_ylabel("Failing tests (error)", fontsize=11)
    cost_delta = hero["cost_usd"]["B20"] - hero["cost_usd"]["LG"]
    ax.set_title(
        f"W1·CG·LangGraph·Hk seed=34 (MBPP/138): LG found the answer at iter 2 and stopped. "
        f"B20 found it at iter 8, then degraded back to broken. ${cost_delta:.4f} saved.",
        fontsize=10.5,
    )
    ax.legend(loc="upper right", fontsize=10)
    ax.grid(alpha=0.3)
    ax.set_xticks(range(1, 21))
    plt.tight_layout()
    plt.savefig(OUT / "hero_seed34.png", dpi=140)
    plt.close()


def main() -> None:
    trials = load_registered_trials()
    verdicts = load_judge_verdicts()
    print(f"loaded {len(trials)} trials, {len(verdicts)} judge verdicts")

    chart_1_cost_by_condition(trials)
    print(f"  → {OUT / 'cost_by_condition.png'}")
    chart_2_winrate_with_ci(trials, verdicts)
    print(f"  → {OUT / 'winrate_with_ci.png'}")
    chart_3_savings_by_segment(trials)
    print(f"  → {OUT / 'savings_by_segment.png'}")
    chart_4_band_emissions(trials)
    print(f"  → {OUT / 'band_emissions.png'}")
    chart_5_lead_time_histogram(trials)
    print(f"  → {OUT / 'lead_time_histogram.png'}")
    chart_6_hero_seed34(trials)
    print(f"  → {OUT / 'hero_seed34.png'}")


if __name__ == "__main__":
    main()
