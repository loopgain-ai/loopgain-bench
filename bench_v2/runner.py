"""Bench v2 Stage-0 runner — orchestrates tasks x tiers, logs JSONL, enforces a
hard spend cap, and applies the strong-oracle re-confirmation gate.

DEFAULT-SAFE: provider defaults to ``mock`` ($0, no network). A real run requires
``--provider {openai,anthropic}`` AND ``--i-understand-this-spends-money``, and
still aborts the moment the running cost estimate would exceed ``--max-spend``.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from typing import Optional

from . import metrics, oracle
from .data import Task, load_bird_minidev, mock_tasks
from .llm import MockProvider, make_provider
from .loop import run_loop


class SpendCapExceeded(RuntimeError):
    pass


def _load_dotenv(path: str = ".env") -> None:
    """Load provider API keys from a local .env into os.environ (no logging of
    values). Only sets keys that are not already present in the environment.
    Done in-process so secrets never appear in a shell command or tool log."""
    if not os.path.exists(path):
        return
    for line in open(path):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY") and not os.environ.get(k):
            os.environ[k] = v


def _cost_from_trials(trials: list, model: str) -> float:
    from .llm import PRICES
    pin, pout = PRICES.get(model, (0.0, 0.0))
    it = sum(t.get("input_tokens", 0) for t in trials)
    ot = sum(t.get("output_tokens", 0) for t in trials)
    return (it * pin + ot * pout) / 1_000_000


def _load_checkpoint(path: Optional[str]) -> tuple[list, set]:
    """Load already-completed trials from a JSONL checkpoint (resume support)."""
    if not path or not os.path.exists(path):
        return [], set()
    trials = []
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        try:
            t = json.loads(line)
            trials.append(t)
        except json.JSONDecodeError:
            continue  # tolerate a truncated final line from a hard kill
    return trials, {t["task_id"] for t in trials}


def run_tier(
    tasks: list[Task],
    provider_kind: str,
    model: Optional[str],
    max_iter: int,
    max_spend: float,
    mock_scripts: Optional[dict] = None,
    checkpoint_path: Optional[str] = None,
) -> dict:
    provider = make_provider(provider_kind, model)

    # Resume: completed trials are streamed to checkpoint_path as JSONL; reload
    # them and skip those task_ids so an interrupted run never re-pays for work.
    trials, done_ids = _load_checkpoint(checkpoint_path)
    if done_ids:
        print(f"  [resume] {len(done_ids)} trials already in {checkpoint_path}; skipping them")
    ckpt = open(checkpoint_path, "a") if checkpoint_path else None

    for task in tasks:
        if task.task_id in done_ids:
            continue
        if isinstance(provider, MockProvider) and mock_scripts is not None:
            provider.load_script(mock_scripts[task.task_id])

        task_error = False
        try:
            tr = run_loop(task, provider, max_iter=max_iter)
        except Exception as e:  # noqa: BLE001 — one bad task must not abort the run
            task_error = True
            from .loop import IterPoint, TrialResult
            tr = TrialResult(task_id=task.task_id, difficulty=task.difficulty)
            tr.trajectory = [IterPoint(0, "", 1, False, f"task-failed: {type(e).__name__}: {e}", "ERR")]
            tr.terminal_s = 1

        s = tr.s_series()
        hashes = [p.result_hash for p in tr.trajectory]
        m = metrics.trial_metrics(s, hashes)
        m.task_id = task.task_id

        reconfirm_rejected = False
        if m.found_then_broke:  # strong-oracle re-confirmation (prereg §5)
            if oracle.reconfirm(tr.trajectory[tr.best_iter].sql, tr.trajectory[-1].sql, task):
                pass
            else:
                m.found_then_broke = False  # flaky/ORDER-BY false degrade — do not count
                reconfirm_rejected = True

        trial = dict(
            task_id=tr.task_id, difficulty=tr.difficulty, s_series=s,
            first_success_iter=tr.first_success_iter, terminal_s=tr.terminal_s,
            input_tokens=tr.input_tokens, output_tokens=tr.output_tokens,
            trajectory=[asdict(p) for p in tr.trajectory],
            metrics=asdict(m), ftb_reconfirm_rejected=reconfirm_rejected,
            task_error=task_error,
        )
        trials.append(trial)
        if ckpt is not None:  # durable per-trial checkpoint: survives a kill
            ckpt.write(json.dumps(trial) + "\n")
            ckpt.flush()
            os.fsync(ckpt.fileno())

        # hard spend cap — check AFTER each task, abort before the next.
        # Cost summed across resume (from per-trial tokens), not just this process.
        spent = _cost_from_trials(trials, provider.model)
        if spent > max_spend:
            if ckpt is not None:
                ckpt.close()
            raise SpendCapExceeded(
                f"spend ${spent:.2f} exceeded cap ${max_spend:.2f} after "
                f"{len(trials)} trials; aborting (no further calls). Re-run to resume."
            )

    if ckpt is not None:
        ckpt.close()

    per_trial_metrics = [metrics.TrialMetrics(**t["metrics"]) for t in trials]
    agg = metrics.aggregate(per_trial_metrics)
    return dict(
        provider=provider_kind, model=provider.model,
        n=len(trials), max_iter=max_iter,
        cost_usd=round(_cost_from_trials(trials, provider.model), 4),
        aggregate=asdict(agg),
        ftb_rate=agg.ftb_rate,
        ftb_confirmed=sum(1 for t in trials if t["metrics"]["found_then_broke"]),
        ftb_rejected_by_reconfirm=sum(1 for t in trials if t.get("ftb_reconfirm_rejected")),
        n_task_errors=sum(1 for t in trials if t.get("task_error")),
        verdict=agg.verdict(),
        trials=trials,
    )


def main(argv=None):
    ap = argparse.ArgumentParser(description="Bench v2 Stage-0 oscillation base-rate runner")
    ap.add_argument("--provider", default="mock", choices=["mock", "openai", "anthropic"])
    ap.add_argument("--model", default=None, help="override model id")
    ap.add_argument("--source", default="mock", choices=["mock", "bird"])
    ap.add_argument("--bird-root", default=os.environ.get("BIRD_MINIDEV_ROOT", ""))
    ap.add_argument("--n", type=int, default=None, help="limit tasks")
    ap.add_argument("--max-iter", type=int, default=10)
    ap.add_argument("--max-spend", type=float, default=80.0, help="hard USD cap")
    ap.add_argument("--sample-seed", type=int, default=20260531, help="deterministic task-sampling seed")
    ap.add_argument("--out", default=None, help="final summary JSON output path")
    ap.add_argument("--checkpoint", default=None,
                    help="per-trial JSONL checkpoint for resume (default: <out>.partial.jsonl)")
    ap.add_argument("--i-understand-this-spends-money", action="store_true")
    args = ap.parse_args(argv)

    if args.provider != "mock" and not args.i_understand_this_spends_money:
        sys.exit("REFUSING: real provider selected without "
                 "--i-understand-this-spends-money. This is a paid run.")

    if args.provider != "mock":
        _load_dotenv()  # populate OPENAI_API_KEY / ANTHROPIC_API_KEY from .env if needed

    if args.source == "mock":
        tasks = mock_tasks()
    else:
        if not args.bird_root:
            sys.exit("--source bird requires --bird-root (see bench_v2/README.md §Data)")
        tasks = load_bird_minidev(args.bird_root, limit=None)  # load all, then sample
    if args.n is not None and args.n < len(tasks):
        # deterministic shuffle so the n-subset spans all DBs/difficulties
        # (BIRD's file is ordered by database; taking the first-n would be DB-skewed)
        import random
        random.Random(args.sample_seed).shuffle(tasks)
        tasks = tasks[: args.n]

    checkpoint = args.checkpoint or (args.out + ".partial.jsonl" if args.out else None)
    result = run_tier(tasks, args.provider, args.model, args.max_iter, args.max_spend,
                      checkpoint_path=checkpoint)
    print(f"[{result['provider']}:{result['model']}] n={result['n']} "
          f"cost=${result['cost_usd']:.4f}  ftb_rate="
          f"{(result['ftb_rate'] or 0)*100:.1f}%  verdict={result['verdict']}")
    print(f"  reached_good={result['aggregate']['n_reached_good']} "
          f"found_then_broke={result['aggregate']['found_then_broke']} "
          f"(reconfirm-rejected={result['ftb_rejected_by_reconfirm']}) "
          f"overshoot={result['aggregate']['overshoot']} "
          f"oscillation={result['aggregate']['oscillation']} "
          f"divergence={result['aggregate']['divergence']}")
    if args.out:
        with open(args.out, "w") as fh:
            json.dump(result, fh, indent=2)
        print(f"  wrote {args.out}")
    return result


if __name__ == "__main__":
    main()
