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


def run_tier(
    tasks: list[Task],
    provider_kind: str,
    model: Optional[str],
    max_iter: int,
    max_spend: float,
    mock_scripts: Optional[dict] = None,
) -> dict:
    provider = make_provider(provider_kind, model)
    trials = []
    per_trial_metrics = []
    ftb_confirmed = 0
    ftb_rejected_by_reconfirm = 0

    n_task_errors = 0
    for task in tasks:
        if isinstance(provider, MockProvider) and mock_scripts is not None:
            provider.load_script(mock_scripts[task.task_id])

        try:
            tr = run_loop(task, provider, max_iter=max_iter)
        except Exception as e:  # noqa: BLE001 — one bad task must not abort the run
            n_task_errors += 1
            from .loop import IterPoint, TrialResult
            tr = TrialResult(task_id=task.task_id, difficulty=task.difficulty)
            tr.trajectory = [IterPoint(0, "", 1, False, f"task-failed: {type(e).__name__}: {e}", "ERR")]
            tr.terminal_s = 1

        # hard spend cap — check AFTER each task, abort before the next
        spent = provider.usage.cost_usd(provider.model)
        if spent > max_spend:
            raise SpendCapExceeded(
                f"spend ${spent:.2f} exceeded cap ${max_spend:.2f} after "
                f"{len(trials)+1} tasks; aborting (no further calls)."
            )

        s = tr.s_series()
        hashes = [p.result_hash for p in tr.trajectory]
        m = metrics.trial_metrics(s, hashes)
        m.task_id = task.task_id

        # Strong-oracle re-confirmation of a found-it-then-broke-it event (prereg §5)
        if m.found_then_broke:
            best_sql = tr.trajectory[tr.best_iter].sql
            term_sql = tr.trajectory[-1].sql
            if oracle.reconfirm(best_sql, term_sql, task):
                ftb_confirmed += 1
            else:
                m.found_then_broke = False  # flaky/ORDER-BY false degrade — do not count
                ftb_rejected_by_reconfirm += 1

        per_trial_metrics.append(m)
        trials.append(
            dict(
                task_id=tr.task_id, difficulty=tr.difficulty, s_series=s,
                first_success_iter=tr.first_success_iter, terminal_s=tr.terminal_s,
                input_tokens=tr.input_tokens, output_tokens=tr.output_tokens,
                trajectory=[asdict(p) for p in tr.trajectory],
                metrics=asdict(m),
            )
        )

    agg = metrics.aggregate(per_trial_metrics)
    return dict(
        provider=provider_kind, model=provider.model,
        n=len(tasks), max_iter=max_iter,
        cost_usd=round(provider.usage.cost_usd(provider.model), 4),
        usage=asdict(provider.usage),
        aggregate=asdict(agg),
        ftb_rate=agg.ftb_rate,
        ftb_confirmed=ftb_confirmed,
        ftb_rejected_by_reconfirm=ftb_rejected_by_reconfirm,
        n_task_errors=n_task_errors,
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
    ap.add_argument("--out", default=None, help="JSONL/JSON output path")
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

    result = run_tier(tasks, args.provider, args.model, args.max_iter, args.max_spend)
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
