"""Analysis pipeline — produces the six required outputs per BENCH_PROTOCOL.md.

Run AFTER `make bench` (or `make dry-run` for a smoke pass). Idempotent on
raw data: re-running produces the same results from the same `data/raw/`
inputs. Per lockdown #9, raw data is never modified; analysis re-reads from
disk each time.

Outputs (per BENCH_PROTOCOL.md §"Pre-registered analysis plan"):
  1. Per-condition aggregates: $ / iters / wall-clock — median + IQR
  2. Quality comparison: LLM-judge winrate LG-vs-B20 (95% bootstrap CI)
                       + programmatic eval pass-rate delta (95% CI)
  3. Failure-mode segmentation: above stats by LG's terminal band
                                (FAST_CONVERGE / CONVERGING / STALLING /
                                 OSCILLATING / DIVERGING / TARGET_MET)
  4. False-stop accounting: trial-level table (stops + B20 counterfactual)
  5. Early-warning lead-time: histogram of first-warn iter vs catastrophe iter
  6. Adapter parity: same task across adapters, stats above

Writes to data/results/:
  - aggregate.csv         (table 1)
  - quality.csv           (table 2)
  - by_band.csv           (table 3)
  - false_stops.csv       (table 4)
  - lead_time.csv         (table 5)
  - adapter_parity.csv    (table 6)
  - summary.html          (rendered for loopgain.ai/benchmarks)

CLI:
    python -m analysis.run --input data/raw/ --output data/results/
    python -m analysis.run --input data/raw/ --tag registered  # filter by tag suffix
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Iterator


def load_trials(input_dir: Path, tag: str | None = None) -> Iterator[dict]:
    """Yield trial records from all JSONL files in input_dir.

    Skips header lines and trial-error records. If `tag` is given, only
    reads files matching `*-{tag}.jsonl`.
    """
    pattern = f"*-{tag}.jsonl" if tag else "*.jsonl"
    for path in sorted(input_dir.glob(pattern)):
        with path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                if rec.get("_header") or rec.get("_trial_error"):
                    continue
                rec["_source_file"] = path.name
                yield rec


def aggregate_per_condition(trials: list[dict]) -> dict:
    """Table 1: $ / iters / wall-clock per condition, median + IQR.

    Returns a nested dict: { condition: { metric: (median, p25, p75) } }.
    """
    out: dict[str, dict] = {}
    for cond in ("B5", "B10", "B20", "LG"):
        costs = [t["cost_usd"][cond] for t in trials if cond in t.get("cost_usd", {})]
        iters = [t["conditions"][cond]["iters"] for t in trials if cond in t.get("conditions", {})]
        walls = [t["conditions"][cond]["wall_clock_s"] for t in trials if cond in t.get("conditions", {})]
        out[cond] = {
            "cost_usd": _med_iqr(costs),
            "iters": _med_iqr(iters),
            "wall_clock_s": _med_iqr(walls),
            "n": len(costs),
        }
    return out


def segment_by_band(trials: list[dict]) -> dict:
    """Table 3: aggregate stats segmented by LoopGain's terminal band.

    Band is read from `conditions.LG.outcome` (which is r.outcome from the
    LoopGain result). TARGET_MET / FAST_CONVERGE / CONVERGING / STALLING /
    OSCILLATING / DIVERGING / MAX_ITERATIONS are the possible values.
    """
    bands: dict[str, list[dict]] = {}
    for t in trials:
        lg = t.get("conditions", {}).get("LG", {})
        band = lg.get("outcome", "UNKNOWN")
        bands.setdefault(band, []).append(t)
    return {band: aggregate_per_condition(ts) for band, ts in bands.items()}


def false_stop_table(trials: list[dict]) -> list[dict]:
    """Table 4: for each LG stop, did B20 produce strictly better output?

    'Strictly better' means: B20.best_error < LG.best_error AND (where
    available) programmatic eval B20 > LG. LLM-judge winrate is computed
    separately by judge_winrate() and merged here once judging is run.

    Stub returns the error-based determination; quality determination
    requires the LLM judge to have already been run on `data/raw/`.
    """
    rows = []
    for t in trials:
        lg = t["conditions"]["LG"]
        b20 = t["conditions"]["B20"]
        # Error-based "better" — provisional. Final determination uses judge.
        error_says_b20_better = b20["best_error"] < lg["best_error"]
        rows.append({
            "trial_id": t["trial_id"],
            "workload": t["workload"],
            "lg_iters": lg["iters"],
            "lg_best_error": lg["best_error"],
            "b20_best_error": b20["best_error"],
            "lg_band": lg.get("outcome"),
            "error_says_b20_better": error_says_b20_better,
            # judge_says_b20_better: filled in after judge run
        })
    return rows


def lead_time_histogram(trials: list[dict]) -> list[dict]:
    """Table 5: for trials where B20 catastrophically diverges (final E_ratio
    > 2.0), the iteration at which LG first emitted a warning band.
    """
    rows = []
    for t in trials:
        b20 = t["conditions"]["B20"]
        lg = t["conditions"]["LG"]
        b20_history = b20.get("error_history", [])
        if not b20_history or b20_history[0] <= 0:
            continue
        b20_e_ratio = b20_history[-1] / b20_history[0]
        if b20_e_ratio <= 2.0:
            continue
        state_history = lg.get("state_history", [])
        warn_bands = {"STALLING", "OSCILLATING", "DIVERGING"}
        first_warn_iter = next(
            (i + 1 for i, s in enumerate(state_history) if s in warn_bands),
            None,
        )
        # Catastrophe = iter where B20's e_ratio first crossed 2.0
        catastrophe_iter = next(
            (i + 1 for i, e in enumerate(b20_history) if e / b20_history[0] > 2.0),
            len(b20_history),
        )
        rows.append({
            "trial_id": t["trial_id"],
            "b20_final_e_ratio": b20_e_ratio,
            "first_warn_iter": first_warn_iter,
            "catastrophe_iter": catastrophe_iter,
            "lead_time": (catastrophe_iter - first_warn_iter) if first_warn_iter else None,
        })
    return rows


def adapter_parity(trials: list[dict]) -> dict:
    """Table 6: same task across adapters. Spread on key metrics.

    Groups trials by workload PREFIX (e.g. 'w1-code') so cells implementing
    the same task with different adapters can be compared.
    """
    by_task: dict[str, dict[str, list[dict]]] = {}
    for t in trials:
        wid = t["workload"]
        # Convention: workload ids are 'wN-<task>-<adapter>-<model>'
        prefix = "-".join(wid.split("-")[:2])  # e.g. "w1-code"
        framework = t.get("framework", "unknown")
        by_task.setdefault(prefix, {}).setdefault(framework, []).append(t)
    return {
        task: {fw: aggregate_per_condition(ts) for fw, ts in fws.items()}
        for task, fws in by_task.items()
    }


def judge_winrate(raw_dir: Path, tag: str | None = None) -> dict:
    """Table 2: LLM-judge pairwise winrate LG vs B20 with 95% bootstrap CI.

    Reads judge comparisons from data/raw/judge-*.jsonl (produced by
    bench/judge.py::pairwise_winrate). Aggregates per-workload AND overall.
    Per BENCH_PROTOCOL.md §"Metrics", LoopGain wins are scored 1.0, ties 0.5,
    B20 wins 0.0; the reported winrate is the mean across comparisons, with
    a 95% percentile bootstrap CI computed inline (no scipy).

    Returns a dict shaped:
        {
            "implemented": True,
            "overall": {n, lg_wins, b20_wins, ties, winrate, ci_lo, ci_hi},
            "by_workload": {workload_id: {…same shape…}},
            "files_read": [str, …],
        }

    If no judge files exist yet, returns a contract-shape stub so the rest
    of the pipeline composes without crashing.
    """
    pattern = f"judge-*-{tag}.jsonl" if tag else "judge-*.jsonl"
    files = sorted(raw_dir.glob(pattern))
    if not files:
        return {
            "implemented": True,
            "overall": None,
            "by_workload": {},
            "files_read": [],
            "note": f"no judge JSONL files matched {pattern!r} in {raw_dir}",
        }

    # Per-file aggregation: each comparison contributes a 1.0 / 0.5 / 0.0 score.
    by_workload: dict[str, list[float]] = {}
    counts: dict[str, dict[str, int]] = {}

    for path in files:
        with path.open() as f:
            header_line = f.readline().strip()
            if not header_line:
                continue
            header = json.loads(header_line)
            if not header.get("_header"):
                # Malformed file — first line should be a header. Surface, do
                # not silently skip (lockdown #5).
                raise ValueError(f"judge file missing header: {path}")
            workload_id = header.get("workload_id", "unknown")
            scores = by_workload.setdefault(workload_id, [])
            ct = counts.setdefault(workload_id, {"lg_wins": 0, "b20_wins": 0, "ties": 0})

            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                if rec.get("_header"):
                    continue
                lg_pos = rec.get("lg_position")
                choice = rec.get("judge_choice")
                if choice == "TIE":
                    scores.append(0.5)
                    ct["ties"] += 1
                elif (choice == "A" and lg_pos == "A") or (choice == "B" and lg_pos == "B"):
                    scores.append(1.0)
                    ct["lg_wins"] += 1
                elif choice in ("A", "B"):
                    scores.append(0.0)
                    ct["b20_wins"] += 1
                else:
                    # Defensive: unknown choice -> treat as TIE.
                    scores.append(0.5)
                    ct["ties"] += 1

    def _summarize(scores: list[float], ct: dict[str, int]) -> dict:
        n = len(scores)
        if n == 0:
            return {"n": 0, "lg_wins": 0, "b20_wins": 0, "ties": 0,
                    "winrate": None, "ci_lo": None, "ci_hi": None}
        winrate = sum(scores) / n
        ci_lo, ci_hi = _bootstrap_ci(scores, n_resamples=5000, seed=0)
        return {
            "n": n,
            "lg_wins": ct["lg_wins"],
            "b20_wins": ct["b20_wins"],
            "ties": ct["ties"],
            "winrate": winrate,
            "ci_lo": ci_lo,
            "ci_hi": ci_hi,
        }

    per_workload = {wid: _summarize(scores, counts[wid]) for wid, scores in by_workload.items()}
    overall_scores = [s for scores in by_workload.values() for s in scores]
    overall_counts = {
        "lg_wins": sum(c["lg_wins"] for c in counts.values()),
        "b20_wins": sum(c["b20_wins"] for c in counts.values()),
        "ties": sum(c["ties"] for c in counts.values()),
    }
    overall = _summarize(overall_scores, overall_counts)

    return {
        "implemented": True,
        "overall": overall,
        "by_workload": per_workload,
        "files_read": [str(p) for p in files],
    }


def _bootstrap_ci(
    scores: list[float], *, n_resamples: int = 5000, seed: int = 0, alpha: float = 0.05
) -> tuple[float, float]:
    """95% percentile bootstrap CI over a 0/0.5/1 score vector. No scipy."""
    import numpy as np

    arr = np.asarray(scores, dtype=float)
    if arr.size == 0:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, arr.size, size=(n_resamples, arr.size))
    means = arr[idx].mean(axis=1)
    lo = float(np.quantile(means, alpha / 2))
    hi = float(np.quantile(means, 1 - alpha / 2))
    return (lo, hi)


# -------------------- helpers --------------------


def _med_iqr(xs: list[float]) -> tuple[float, float, float] | None:
    if not xs:
        return None
    xs_sorted = sorted(xs)
    n = len(xs_sorted)
    median = statistics.median(xs_sorted)
    p25 = xs_sorted[n // 4]
    p75 = xs_sorted[(3 * n) // 4]
    return (median, p25, p75)


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("# no rows\n")
        return
    import csv
    keys = sorted({k for r in rows for k in r.keys()})
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    p = argparse.ArgumentParser(description="LoopGain Bench analysis")
    p.add_argument("--input", type=Path, default=Path("data/raw"))
    p.add_argument("--output", type=Path, default=Path("data/results"))
    p.add_argument("--tag", default=None, help="Filter to *-{tag}.jsonl files")
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    trials = list(load_trials(args.input, args.tag))
    print(f"Loaded {len(trials)} trials from {args.input}")
    if not trials:
        print("No trials to analyze. Run `make dry-run` or `make bench` first.")
        return

    # Tables 1-6
    agg = aggregate_per_condition(trials)
    (args.output / "aggregate.json").write_text(json.dumps(agg, indent=2))
    print(f"  → {args.output / 'aggregate.json'}")

    by_band = segment_by_band(trials)
    (args.output / "by_band.json").write_text(json.dumps(by_band, indent=2))
    print(f"  → {args.output / 'by_band.json'}")

    fs_rows = false_stop_table(trials)
    _write_csv(args.output / "false_stops.csv", fs_rows)
    print(f"  → {args.output / 'false_stops.csv'} ({len(fs_rows)} rows)")

    lt_rows = lead_time_histogram(trials)
    _write_csv(args.output / "lead_time.csv", lt_rows)
    print(f"  → {args.output / 'lead_time.csv'} ({len(lt_rows)} rows)")

    parity = adapter_parity(trials)
    (args.output / "adapter_parity.json").write_text(json.dumps(parity, indent=2))
    print(f"  → {args.output / 'adapter_parity.json'}")

    judge = judge_winrate(args.input, args.tag)
    (args.output / "judge.json").write_text(json.dumps(judge, indent=2))
    n_files = len(judge.get("files_read") or [])
    overall = judge.get("overall")
    if overall:
        print(
            f"  → {args.output / 'judge.json'} "
            f"({n_files} judge file(s), n={overall['n']}, "
            f"winrate={overall['winrate']:.3f}, "
            f"CI=[{overall['ci_lo']:.3f}, {overall['ci_hi']:.3f}])"
        )
    else:
        print(
            f"  → {args.output / 'judge.json'} "
            f"(no judge JSONL yet — run bench/judge.py::pairwise_winrate after `make bench`)"
        )

    print("done.")


if __name__ == "__main__":
    main()
