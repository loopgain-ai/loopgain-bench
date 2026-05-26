"""One-time uploader: bench raw JSONL → loopgain telemetry receiver.

Reads every ``data/raw/*-registered.jsonl`` file in the bench repo,
extracts the LG-condition record from each trial, transforms it into the
v3 telemetry aggregate payload that ``LoopGain.send_telemetry()`` would
produce for a real loop, and POSTs it to ``LOOPGAIN_TELEMETRY_ENDPOINT``.

Skips ``B5`` / ``B10`` / ``B20`` baseline conditions — they aren't
LoopGain runs and don't belong in a LoopGain dashboard.

The bench harness did not emit telemetry at runtime; this script back-
fills the canonical bench-run dataset into the public benchmark tenant
``cust_7931de9f766452ac`` so the dashboard ``/benchmark`` route renders
something. One-shot — there's no plan to keep this running.

Defaults: streams every registered file, writes live to telemetry, four
concurrent workers, exponential backoff on 429/5xx. Use ``--dry-run`` to
build payloads without sending, and ``--limit N`` for smoke tests.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import os
import ssl
import statistics
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

try:
    import certifi  # type: ignore

    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:  # pragma: no cover — certifi is in the venv
    _SSL_CTX = ssl.create_default_context()


REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data" / "raw"

# Pinned to the bench run under test. The bench publishes loopgain==0.2.0
# results regardless of the library's current release version.
LIBRARY_VERSION = "0.2.0"
LIBRARY_NAME = "loopgain"
SCHEMA_VERSION = 3

# Mirror the library's defaults so the per-loop thresholds column is
# populated with sensible values. Bench used the stock LoopGain defaults
# (no custom thresholds), so these match the library at v0.2.0.
DEFAULT_THRESHOLDS = {
    "fast_converge": 0.3,
    "converging": 0.85,
    "stalling": 0.95,
    "oscillating_upper": 1.05,
}
DEFAULT_SMOOTHING_WINDOW = 3
PER_ITERATION_CAP = 256


def safe_float(x: Any) -> float | None:
    """Coerce inf / -inf / NaN to None — strict JSON does not allow them."""
    if x is None:
        return None
    if isinstance(x, (int, float)) and not math.isfinite(float(x)):
        return None
    return float(x)


def _ema(values: list[float], window: int) -> list[float]:
    """EMA matching loopgain.core: alpha = 2/(window+1), seeded with first value."""
    if not values:
        return []
    alpha = 2.0 / (window + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(alpha * v + (1.0 - alpha) * out[-1])
    return out


def synthesize_ab_profile(error_history: list[float]) -> list[float]:
    """Reconstruct the smoothed Aβ trajectory from the bench's error history.

    The bench JSONLs store ``error_history`` but not the per-iteration
    Aβ values the library would normally capture. Aβ_raw_i = |e_i / e_{i-1}|
    is the Barkhausen ratio used by ``loopgain.core``; we apply the same
    EMA smoothing the library applies (window=3) so the resulting profile
    is comparable to a live loop's. Length = len(error_history) - 1.

    Division by zero (a previous error of exactly 0) is collapsed to 0.0
    rather than +inf so the receiver's finite-number validator accepts it.
    """
    raw: list[float] = []
    for i in range(1, len(error_history)):
        prev = error_history[i - 1]
        curr = error_history[i]
        if prev == 0:
            raw.append(0.0)
            continue
        ratio = abs(curr / prev)
        if not math.isfinite(ratio):
            raw.append(0.0)
        else:
            raw.append(ratio)
    return _ema(raw, DEFAULT_SMOOTHING_WINDOW)


def trial_to_payload(
    trial: dict[str, Any],
    cell_id: str,
    framework: str | None,
    loop_type: str | None,
    timestamp: datetime,
) -> dict[str, Any]:
    """Build a v3 telemetry payload for a single trial's LG condition."""
    lg = trial["conditions"]["LG"]
    iters = int(lg["iters"])
    raw_errors = [float(e) for e in lg.get("error_history", [])]
    errors = [e for e in raw_errors if math.isfinite(e)]
    ab_profile = synthesize_ab_profile(errors)

    # Real measured $ saved + $ spent on this trial. The bench has both
    # numbers because every workload ran under paired B20 + LG conditions;
    # ordinary customers don't, so both fields are bench-specific and the
    # receiver/dashboard treat them as optional. actual_dollars_spent is
    # just lg_cost — the LG-side cost of this run, surfaced directly so
    # the dashboard can render measured spend instead of iter × $/iter
    # extrapolation. Saved is clamped at 0 so a rounding artifact never
    # produces a negative "saving"; spent gets the same defensive clamp.
    cost_usd = trial.get("cost_usd") or {}
    b20_cost = cost_usd.get("B20")
    lg_cost = cost_usd.get("LG")
    if isinstance(b20_cost, (int, float)) and isinstance(lg_cost, (int, float)):
        actual_dollars_saved: float | None = max(0.0, float(b20_cost) - float(lg_cost))
    else:
        actual_dollars_saved = None
    if isinstance(lg_cost, (int, float)):
        actual_dollars_spent: float | None = max(0.0, float(lg_cost))
    else:
        actual_dollars_spent = None

    if ab_profile:
        profile_summary = {
            "min": safe_float(min(ab_profile)),
            "max": safe_float(max(ab_profile)),
            "median": safe_float(statistics.median(ab_profile)),
            "samples": len(ab_profile),
        }
    else:
        profile_summary = {"min": None, "max": None, "median": None, "samples": 0}

    outcome = lg["outcome"]
    rollback_triggered = outcome in ("oscillating", "diverged")

    # Bench's headline baseline was max_iter=20 (see BENCH_PROTOCOL.md
    # §Baselines and the landing-page "93.5% cost reduction" claim).
    # Per-tenant convention: real customers see vs the library default (10);
    # the bench tenant uses 20 because that's what the bench measured.
    savings_vs_fixed_cap = max(0, 20 - iters)

    hour_bucket = timestamp.replace(
        minute=0, second=0, microsecond=0
    ).isoformat()

    workload_id = trial.get("trial_id") or cell_id

    truncated = (
        len(errors) > PER_ITERATION_CAP or len(ab_profile) > PER_ITERATION_CAP
    )
    per_iteration = {
        "error_history": errors[:PER_ITERATION_CAP],
        "convergence_profile": ab_profile[:PER_ITERATION_CAP],
        "truncated": truncated,
        "cap": PER_ITERATION_CAP,
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "library": LIBRARY_NAME,
        "library_version": LIBRARY_VERSION,
        "workload_id": workload_id,
        "timestamp_hour": hour_bucket,
        "framework": framework,
        "loop_type": loop_type,
        "team": "bench",
        "loop": {
            "outcome": outcome,
            "iterations_used": iters,
            "gain_margin": safe_float(lg.get("gain_margin")),
            "savings_vs_fixed_cap": savings_vs_fixed_cap,
            "convergence_profile_summary": profile_summary,
            "rollback_triggered": rollback_triggered,
            "first_eta_prediction": None,
            "first_eta_at_iteration": None,
        },
        "thresholds": DEFAULT_THRESHOLDS,
        "smoothing_window": DEFAULT_SMOOTHING_WINDOW,
        "per_iteration": per_iteration,
        "actual_dollars_saved": actual_dollars_saved,
        "actual_dollars_spent": actual_dollars_spent,
    }


@dataclass
class TrialRecord:
    """One trial extracted from a registered JSONL, ready to be posted."""

    payload: dict[str, Any]
    source_file: str
    trial_id: str


def iter_trials(limit: int | None = None) -> Iterator[TrialRecord]:
    """Yield TrialRecords for every LG trial in every registered JSONL.

    Skips judge-* files (LLM-judge sweeps, not core bench runs) and
    skips trials whose ``LG`` condition is missing.
    """
    files = sorted(
        p for p in DATA_DIR.glob("*-registered.jsonl")
        if not p.name.startswith("judge-")
    )
    yielded = 0
    for f in files:
        cell_id: str | None = None
        framework: str | None = None
        loop_type: str | None = None
        started_utc: datetime = datetime.now(timezone.utc)
        with open(f) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                if rec.get("_header"):
                    cell = rec.get("cell", {})
                    cell_id = cell.get("id")
                    framework = cell.get("framework")
                    loop_type = cell.get("loop_type")
                    ts = rec.get("started_utc")
                    if ts:
                        # JSONL writes "...Z"; fromisoformat handles "+00:00" form.
                        started_utc = datetime.fromisoformat(
                            ts.replace("Z", "+00:00")
                        )
                    continue
                if "conditions" not in rec or "LG" not in rec.get("conditions", {}):
                    continue
                if cell_id is None:
                    cell_id = rec.get("workload", "unknown")
                payload = trial_to_payload(
                    rec, cell_id, framework, loop_type, started_utc
                )
                yield TrialRecord(
                    payload=payload,
                    source_file=f.name,
                    trial_id=rec.get("trial_id", "?"),
                )
                yielded += 1
                if limit is not None and yielded >= limit:
                    return


def post_one(
    endpoint: str, token: str, payload: dict[str, Any], timeout: float = 10.0
) -> tuple[int, str]:
    """POST a single payload; return (status, body) where status==-1 means transport error."""
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": f"loopgain-bench-uploader/{LIBRARY_VERSION}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        try:
            txt = e.read().decode("utf-8", errors="replace")
        except Exception:
            txt = str(e)
        return e.code, txt
    except Exception as e:
        return -1, repr(e)


def post_with_retry(
    endpoint: str,
    token: str,
    payload: dict[str, Any],
    max_attempts: int = 12,
    max_backoff: float = 60.0,
) -> tuple[int, str]:
    """POST with exponential backoff on 429/5xx/transport errors.

    Cloudflare's per-customer rate limit on /v1/aggregate has a window
    measured in tens of seconds; the retry budget here gives each request
    well over a minute of cumulative wait to recover, which is enough
    headroom for the canonical 2,000-trial back-fill.
    """
    delay = 1.0
    last: tuple[int, str] = (-1, "no attempts")
    for attempt in range(max_attempts):
        status, body = post_one(endpoint, token, payload)
        if 200 <= status < 300:
            return status, body
        last = (status, body)
        retriable = status == 429 or (500 <= status < 600) or status == -1
        if not retriable:
            return status, body
        time.sleep(delay)
        delay = min(delay * 2, max_backoff)
    return last


def load_env() -> tuple[str, str]:
    """Read endpoint+token from .env (one of the simplest dotenv parsers)."""
    env_path = REPO_ROOT / ".env"
    endpoint = os.environ.get("LOOPGAIN_TELEMETRY_ENDPOINT")
    token = os.environ.get("LOOPGAIN_TELEMETRY_TOKEN")
    if env_path.exists() and (not endpoint or not token):
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k == "LOOPGAIN_TELEMETRY_ENDPOINT" and not endpoint:
                endpoint = v
            elif k == "LOOPGAIN_TELEMETRY_TOKEN" and not token:
                token = v
    if not endpoint or not token:
        raise SystemExit(
            "Missing LOOPGAIN_TELEMETRY_ENDPOINT or LOOPGAIN_TELEMETRY_TOKEN "
            "in env / .env"
        )
    return endpoint, token


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Build and validate payloads but do not POST.",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only process the first N trials (across all files).",
    )
    ap.add_argument(
        "--concurrency",
        type=int,
        default=4,
        help="Max parallel POSTs (default 4).",
    )
    ap.add_argument(
        "--print-first",
        action="store_true",
        help="Print the first built payload to stdout and exit.",
    )
    args = ap.parse_args(argv)

    if args.print_first:
        rec = next(iter_trials(limit=1), None)
        if rec is None:
            print("No trials found.")
            return 1
        print(json.dumps(rec.payload, indent=2, default=str))
        return 0

    endpoint, token = load_env()
    print(f"Endpoint: {endpoint}")
    print(f"Dry-run: {args.dry_run}  Limit: {args.limit}  Concurrency: {args.concurrency}")

    trials = list(iter_trials(limit=args.limit))
    print(f"Trials to upload: {len(trials)}")

    if args.dry_run:
        print("[dry-run] skipping POSTs.")
        return 0

    started = time.time()
    sent = 0
    failed: list[tuple[str, int, str]] = []
    lock_state = {"sent": 0, "failed": 0, "last_print": 0.0}

    def worker(rec: TrialRecord) -> tuple[TrialRecord, int, str]:
        status, body = post_with_retry(endpoint, token, rec.payload)
        return rec, status, body

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        futures = [ex.submit(worker, r) for r in trials]
        for fut in concurrent.futures.as_completed(futures):
            rec, status, body = fut.result()
            if 200 <= status < 300:
                sent += 1
                lock_state["sent"] += 1
            else:
                failed.append((rec.trial_id, status, body[:200]))
                lock_state["failed"] += 1
            now = time.time()
            if now - lock_state["last_print"] > 2.0:
                lock_state["last_print"] = now
                done = sent + len(failed)
                print(
                    f"  progress: {done}/{len(trials)} "
                    f"(ok={sent}, failed={len(failed)})"
                )

    elapsed = time.time() - started
    print(
        f"Done. sent={sent} failed={len(failed)} elapsed={elapsed:.1f}s "
        f"({sent / max(elapsed, 1e-6):.1f} trials/s)"
    )
    if failed:
        print("First 10 failures:")
        for t, s, b in failed[:10]:
            print(f"  {t} -> status={s}  body={b}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
