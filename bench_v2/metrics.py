"""Frozen oscillation metrics for Bench v2 (prereg §4).

All operate on a per-trial error series s_t in {0,1} (0 = correct), plus the
per-iteration result hashes for oscillation cycle detection. The HEADLINE
metric is found-it-then-broke-it; the decision thresholds (15% / 5%) are frozen
in the prereg and applied here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

DELTA = 0.1          # overshoot margin on the error scale (any rise on a 0/1 scale)
DIVERGENCE_K = 3     # trailing run length for divergence


@dataclass
class TrialMetrics:
    task_id: str
    reached_good: bool          # ever hit s==0
    found_then_broke: bool      # reached good at t<T, terminal wrong
    overshoot: bool             # after best, some later s >= best + DELTA
    oscillation: bool           # result cycles ans(t)==ans(t-2)!=ans(t-1)
    divergence: bool            # trailing DIVERGENCE_K wrong after a prior correct


def trial_metrics(s_series: list[int], result_hashes: list[str]) -> TrialMetrics:
    return TrialMetrics(
        task_id="",
        reached_good=_reached_good(s_series),
        found_then_broke=_found_then_broke(s_series),
        overshoot=_overshoot(s_series),
        oscillation=_oscillation(result_hashes),
        divergence=_divergence(s_series),
    )


def _reached_good(s: list[int]) -> bool:
    return any(v == 0 for v in s)


def _found_then_broke(s: list[int]) -> bool:
    if not s:
        return False
    first = next((i for i, v in enumerate(s) if v == 0), None)
    if first is None or first == len(s) - 1:
        return False
    return s[-1] != 0


def _overshoot(s: list[int]) -> bool:
    best = None
    for v in s:
        if best is None or v < best:
            best = v
        elif v >= best + DELTA:
            return True
    return False


def _oscillation(h: list[str]) -> bool:
    # answer cycles back: ans(t) == ans(t-2) != ans(t-1)
    for t in range(2, len(h)):
        if h[t] == h[t - 2] and h[t] != h[t - 1]:
            return True
    return False


def _divergence(s: list[int]) -> bool:
    if 0 not in s:
        return False
    first0 = s.index(0)
    tail = s[first0 + 1:]
    if len(tail) < DIVERGENCE_K:
        return False
    return all(v == 1 for v in tail[-DIVERGENCE_K:])


# --------------------------------------------------------------------------
# Aggregation + frozen verdict
# --------------------------------------------------------------------------
@dataclass
class Aggregate:
    n: int
    n_reached_good: int
    found_then_broke: int
    overshoot: int
    oscillation: int
    divergence: int

    @property
    def ftb_rate(self) -> Optional[float]:
        # denominator = tasks that ever reached a good state (prereg §4)
        return (self.found_then_broke / self.n_reached_good) if self.n_reached_good else None

    def verdict(self) -> str:
        r = self.ftb_rate
        if r is None:
            return "N/A (no task reached a good state)"
        pct = 100 * r
        if pct >= 15:
            return f"HEADLINE ({pct:.1f}% >= 15%)"
        if pct < 5:
            return f"NICHE ({pct:.1f}% < 5%)"
        return f"WORKLOAD-DEPENDENT ({pct:.1f}% in [5,15))"


def aggregate(per_trial: list[TrialMetrics]) -> Aggregate:
    return Aggregate(
        n=len(per_trial),
        n_reached_good=sum(m.reached_good for m in per_trial),
        found_then_broke=sum(m.found_then_broke for m in per_trial),
        overshoot=sum(m.overshoot for m in per_trial),
        oscillation=sum(m.oscillation for m in per_trial),
        divergence=sum(m.divergence for m in per_trial),
    )
