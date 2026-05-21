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
    """Table 2: LLM-judge pairwise winrate LG vs B20 with bootstrap CI.

    Reads judge comparisons from data/raw/judge-*.jsonl (produced by a
    separate `make judge` step that the implementation session adds). Stub
    returns a contract-shape dict so the rest of the pipeline composes.
    """
    return {
        "implemented": False,
        "note": "judge_winrate stub — implemented in the Code session along with bench/judge.py",
    }


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
    print(f"  → {args.output / 'judge.json'} (stub — implement in Code session)")

    print("done.")


if __name__ == "__main__":
    main()
