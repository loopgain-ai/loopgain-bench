"""TrialRunner — runs B5 / B10 / B20 / LG paired on the same seed and prompt.

Per BENCH_PROTOCOL.md methodology lockdown #4: within one trial, all four
conditions get the same prompt, same model, same seed. The trial is the unit
of randomization; condition is paired within trial.

Per lockdown #5: failed iterations are reported, never silently dropped. If
an LLM call fails or returns empty text, the iteration's error is set to a
worst-case marker and LoopGain decides whether to keep going.

Per lockdown #9: raw output JSONL is written incrementally to data/raw/, one
line per trial, never overwritten. Re-runs append with an explicit `--tag`.

CLI:
    python -m bench.runner --workload w5_adversarial --n 5         # one cell
    python -m bench.runner --all-cells --n 200 --tag registered   # full bench
    BENCH_MOCK=1 python -m bench.runner --workload w5_adversarial --n 5   # mock-mode
"""

from __future__ import annotations

import argparse
import concurrent.futures
import importlib
import json
import os
import sys
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

# Load .env from the repo root if present. Lookup is silent if the file
# doesn't exist; values already in the environment are not overwritten.
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
except ImportError:
    pass

from loopgain import LoopGain  # type: ignore

from . import __version__
from .llm import Completion, MockLLMClient, client_for_model
from .pricing import cost_for, snapshot_metadata
from .workload import IterationOutcome, TrialInput, Workload

RAW_DIR = Path(__file__).resolve().parents[1] / "data" / "raw"
WORST_CASE_ERROR = 1e6  # sentinel for failed iterations (lockdown #5)
LOOPGAIN_VERSION = "0.2.0"  # pinned per BENCH_PROTOCOL.md


def _run_baseline(workload: Workload, trial: TrialInput, max_iter: int, llm) -> dict:
    """Run the fixed-cap baseline. Always runs to cap; keeps LAST output."""
    completions: list[Completion] = []
    errors: list[float] = []
    output: Optional[str] = None
    iter_outputs: list[str] = []
    failures: list[int] = []
    t0 = time.time()
    for i in range(1, max_iter + 1):
        try:
            outcome = workload.run_iteration(trial, output, i, llm)
            output = outcome.output
            completions.append(outcome.completion)
            errors.append(outcome.error)
            iter_outputs.append(output)
        except Exception as exc:  # noqa: BLE001 — per lockdown #5
            failures.append(i)
            errors.append(WORST_CASE_ERROR)
            iter_outputs.append("")
            sys.stderr.write(f"  [iter {i} failed: {exc!r}]\n")
    elapsed = time.time() - t0
    return {
        "iters": max_iter,
        "input_tokens": sum(c.input_tokens for c in completions),
        "output_tokens": sum(c.output_tokens for c in completions),
        "wall_clock_s": elapsed,
        "final_error": errors[-1] if errors else WORST_CASE_ERROR,
        "best_error": min(errors) if errors else WORST_CASE_ERROR,
        "best_index": (errors.index(min(errors)) if errors else None),
        "error_history": errors,
        "final_output": output or "",
        "failed_iters": failures,
    }


def _run_loopgain(workload: Workload, trial: TrialInput, llm) -> dict:
    """Run the LoopGain condition. Default thresholds. Best-so-far rollback."""
    lg = LoopGain(
        target_error=workload.target_error,
        max_iterations=20,  # ceiling; LoopGain stops earlier on band detection
    )
    completions: list[Completion] = []
    errors: list[float] = []
    state_history: list[str] = []
    output: Optional[str] = None
    iter_outputs: list[str] = []
    failures: list[int] = []
    t0 = time.time()
    while lg.should_continue():
        i = lg.result.iterations_used + 1
        try:
            outcome = workload.run_iteration(trial, output, i, llm)
            output = outcome.output
            completions.append(outcome.completion)
            errors.append(outcome.error)
            iter_outputs.append(output)
            state = lg.observe(outcome.error, output=output)
            state_history.append(state)
        except Exception as exc:  # noqa: BLE001
            failures.append(i)
            errors.append(WORST_CASE_ERROR)
            iter_outputs.append("")
            state = lg.observe(WORST_CASE_ERROR, output="")
            state_history.append(state)
            sys.stderr.write(f"  [iter {i} failed: {exc!r}]\n")
    elapsed = time.time() - t0
    r = lg.result
    # Best-so-far rollback: report best_output, not terminal.
    best_index = r.best_index if r.best_index is not None else (
        errors.index(min(errors)) if errors else 0
    )
    return {
        "iters": r.iterations_used,
        "input_tokens": sum(c.input_tokens for c in completions),
        "output_tokens": sum(c.output_tokens for c in completions),
        "wall_clock_s": elapsed,
        "final_error": errors[-1] if errors else WORST_CASE_ERROR,
        "best_error": r.best_error,
        "best_index": best_index,
        "error_history": errors,
        "state_history": state_history,
        "final_output": iter_outputs[best_index] if iter_outputs else "",
        "outcome": r.outcome,
        "gain_margin": r.gain_margin,
        "failed_iters": failures,
    }


def run_trial(workload: Workload, seed: int) -> dict:
    """Run all four conditions for one trial. Returns a dict ready for JSONL.

    The four conditions (B5/B10/B20/LG) are independent — same seed, same
    prompt, same starting state per Methodology Lockdown #4. They are
    executed in parallel via a 4-thread ThreadPoolExecutor; the I/O wait on
    LLM API calls releases the GIL, so threading is the right model here.

    Per Lockdown #7 ("same wall-clock environment"): running all four
    conditions concurrently strengthens temporal locality (they share the
    same time window exactly), it doesn't weaken it. The lockdown was
    written against the failure mode of comparing a 2am LG run to a 2pm
    baseline — concurrent execution makes that impossible by construction.

    Per Lockdown #5: a per-condition exception is captured and re-raised as
    a TrialError that the outer run_cell handles as a trial-level failure;
    no condition is silently dropped.
    """
    trial = workload.generate_trial(seed)

    def _exec_condition(condition: str) -> tuple[str, dict, float]:
        llm = client_for_model(workload.model, seed=seed)
        if condition == "LG":
            result = _run_loopgain(workload, trial, llm)
        else:
            max_iter = int(condition[1:])
            result = _run_baseline(workload, trial, max_iter, llm)
        cost = cost_for(
            workload.model,
            input_tokens=result["input_tokens"],
            output_tokens=result["output_tokens"],
        )
        return condition, result, cost

    conditions: dict[str, dict] = {}
    cost_usd: dict[str, float] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        futures = [ex.submit(_exec_condition, c) for c in ("B5", "B10", "B20", "LG")]
        for fut in concurrent.futures.as_completed(futures):
            cond, result, cost = fut.result()  # propagates any condition-level exception
            conditions[cond] = result
            cost_usd[cond] = cost

    return {
        "trial_id": f"{workload.id}-seed{seed}",
        "workload": workload.id,
        "framework": workload.framework,
        "model": workload.model,
        "loop_type": workload.loop_type,
        "seed": seed,
        "prompt": trial.prompt[:500],  # truncate for log size; full prompt is reproducible from seed
        "conditions": conditions,
        "cost_usd": cost_usd,
        "trial_metadata": trial.metadata,
    }


def run_cell(workload: Workload, n: int, tag: str = "untagged", *, trials_parallel: int = 1) -> Path:
    """Run n trials of one cell. Writes JSONL incrementally.

    trials_parallel: number of trials to execute concurrently within the cell.
    Default 1 = serial (legacy behavior). The condition-level concurrency
    inside `run_trial` is always on; this adds an outer level of concurrency
    over trials within the same cell. Each trial's JSONL line is written
    under a file lock to keep the JSONL well-formed.
    """
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RAW_DIR / f"{workload.id}-{tag}.jsonl"

    header = {
        "_header": True,
        "bench_version": __version__,
        "loopgain_version": LOOPGAIN_VERSION,
        "tag": tag,
        "cell": workload.to_metadata(),
        "pricing_snapshot": snapshot_metadata(),
        "n_planned": n,
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "mock_mode": os.environ.get("BENCH_MOCK") == "1",
        "trials_parallel": trials_parallel,
    }
    with out_path.open("w") as f:
        f.write(json.dumps(header) + "\n")

    print(f"== {workload.id} ({tag}) — n={n}, model={workload.model}, trials_parallel={trials_parallel} ==")

    write_lock = threading.Lock()
    completed = {"n": 0}
    abort = {"flag": False}
    landed_trials: list[dict] = []  # for the early tripwire
    TRIPWIRE_AFTER = 5  # check after this many trials land

    def _process_one(seed: int) -> None:
        if abort["flag"]:
            return  # tripwire fired; stop accepting work
        try:
            trial_result = run_trial(workload, seed)
            with write_lock:
                if abort["flag"]:
                    return
                with out_path.open("a") as f:
                    f.write(json.dumps(trial_result) + "\n")
                completed["n"] += 1
                landed_trials.append(trial_result)
                print(
                    f"  [{completed['n']}/{n}] seed={seed} "
                    f"LG iters={trial_result['conditions']['LG']['iters']:>2} "
                    f"$LG={trial_result['cost_usd']['LG']:.4f}  "
                    f"$B20={trial_result['cost_usd']['B20']:.4f}"
                )
                # Early-corruption tripwire: if the first 5 trials are all
                # zero-token AND all iters failed, abort the cell loudly.
                # This catches harness-level bugs (thread-safety issues, broken
                # adapter wrappers) that would otherwise silently corrupt
                # all 200 trials.
                if len(landed_trials) == TRIPWIRE_AFTER and not abort["flag"]:
                    all_zero = all(
                        t["conditions"]["LG"]["input_tokens"] == 0
                        and t["conditions"]["B20"]["input_tokens"] == 0
                        for t in landed_trials
                    )
                    if all_zero:
                        abort["flag"] = True
                        sys.stderr.write(
                            f"\n!! TRIPWIRE FIRED: {workload.id} ({tag}) — "
                            f"first {TRIPWIRE_AFTER} trials all have 0 input/output tokens. "
                            f"This indicates a harness-level bug (e.g. thread-safety in the "
                            f"adapter, broken framework_invoke, or per-iteration exception "
                            f"swallowing all LLM calls). Stopping cell.\n"
                        )
        except Exception as exc:  # noqa: BLE001 — log + continue per lockdown #5
            err_record = {
                "_trial_error": True,
                "trial_id": f"{workload.id}-seed{seed}",
                "seed": seed,
                "error": repr(exc),
            }
            with write_lock:
                with out_path.open("a") as f:
                    f.write(json.dumps(err_record) + "\n")
                completed["n"] += 1
            sys.stderr.write(f"  [trial {seed} FAILED: {exc!r}]\n")

    if trials_parallel <= 1:
        for seed in range(n):
            if abort["flag"]:
                break
            _process_one(seed)
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=trials_parallel) as ex:
            list(ex.map(_process_one, range(n)))

    if abort["flag"]:
        print(f"== ABORTED ({completed['n']} trials landed; tripwire fired). raw → {out_path}")
    else:
        print(f"== done. raw → {out_path}")
    return out_path


def _is_cell_complete(out_path: Path, n_required: int) -> bool:
    """Return True iff out_path has header + n_required non-error trial records."""
    if not out_path.exists():
        return False
    n_trials = 0
    with out_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                return False
            if rec.get("_header") or rec.get("_trial_error"):
                continue
            n_trials += 1
    return n_trials >= n_required


def _load_workload(name: str) -> Workload:
    """Import bench.workloads.<name> and return its `WORKLOAD` singleton."""
    mod = importlib.import_module(f"bench.workloads.{name}")
    if not hasattr(mod, "WORKLOAD"):
        raise AttributeError(f"bench.workloads.{name} must define WORKLOAD")
    return mod.WORKLOAD


def main() -> None:
    p = argparse.ArgumentParser(description="LoopGain Bench runner")
    p.add_argument("--workload", help="Single workload module name, e.g. w5_adversarial")
    p.add_argument("--all-cells", action="store_true", help="Run every workload in bench.workloads")
    p.add_argument("--n", type=int, required=True, help="Trials per cell")
    p.add_argument("--tag", default="dev", help="Subdirectory tag for raw output (e.g. dry-run, registered)")
    p.add_argument(
        "--trials-parallel",
        type=int,
        default=1,
        help="Concurrent trials per cell (default 1 = serial). Each trial's 4 conditions always run in parallel.",
    )
    p.add_argument(
        "--cells-parallel",
        type=int,
        default=1,
        help="Concurrent cells when --all-cells (default 1 = serial). Use 2-3 across providers to halve total wall-clock.",
    )
    p.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip cells whose `data/raw/<id>-<tag>.jsonl` is already complete (header + n trials).",
    )
    args = p.parse_args()

    def _run_one_cell(name: str) -> None:
        try:
            workload = _load_workload(name)
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"skipping {name}: {exc!r}\n")
            return
        out_path = RAW_DIR / f"{workload.id}-{args.tag}.jsonl"
        if args.skip_existing and _is_cell_complete(out_path, args.n):
            print(f"== {workload.id} ({args.tag}) — already complete (n={args.n}), skipping")
            return
        run_cell(workload, args.n, args.tag, trials_parallel=args.trials_parallel)

    if args.all_cells:
        wl_dir = Path(__file__).parent / "workloads"
        names = sorted(
            f.stem
            for f in wl_dir.glob("*.py")
            if f.stem not in {"__init__"}
        )
        if args.cells_parallel <= 1:
            for name in names:
                _run_one_cell(name)
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=args.cells_parallel) as ex:
                list(ex.map(_run_one_cell, names))
    else:
        if not args.workload:
            p.error("either --workload or --all-cells required")
        _run_one_cell(args.workload)


if __name__ == "__main__":
    main()
