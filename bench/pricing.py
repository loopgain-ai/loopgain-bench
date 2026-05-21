"""Token-cost computation against the frozen pricing snapshot.

Per BENCH_PROTOCOL.md methodology lockdown #1: prices are loaded from
prices.json at the repo root and never modified during a run. Cost computation
is deterministic given (model, input_tokens, output_tokens).

No batch-API discount, no prompt-caching discount unless explicitly enabled
and disclosed at the cell level.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PRICES_PATH = Path(__file__).resolve().parents[1] / "prices.json"


@dataclass(frozen=True)
class ModelPrice:
    name: str
    input_per_million_usd: float
    output_per_million_usd: float
    cache_read_per_million_usd: float | None = None
    cache_write_per_million_usd: float | None = None


@lru_cache(maxsize=1)
def load_prices() -> dict[str, ModelPrice]:
    """Load prices.json once. Cached for the lifetime of the process."""
    with PRICES_PATH.open() as f:
        data = json.load(f)
    out: dict[str, ModelPrice] = {}
    for name, raw in data["models"].items():
        out[name] = ModelPrice(
            name=name,
            input_per_million_usd=float(raw["input_per_million_usd"]),
            output_per_million_usd=float(raw["output_per_million_usd"]),
            cache_read_per_million_usd=(
                float(raw["cache_read_per_million_usd"])
                if raw.get("cache_read_per_million_usd") is not None
                else None
            ),
            cache_write_per_million_usd=(
                float(raw["cache_write_per_million_usd"])
                if raw.get("cache_write_per_million_usd") is not None
                else None
            ),
        )
    return out


def cost_for(
    model: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float:
    """Return USD cost for one model call. Cache discounts only applied when
    cache_*_tokens is explicitly non-zero AND the model has cache pricing."""
    prices = load_prices()
    if model not in prices:
        raise KeyError(
            f"Model {model!r} not in prices.json. Add it and re-snapshot, "
            f"do not silently extrapolate."
        )
    p = prices[model]
    cost = (
        input_tokens * p.input_per_million_usd
        + output_tokens * p.output_per_million_usd
    ) / 1_000_000
    if cache_read_tokens and p.cache_read_per_million_usd is not None:
        cost += cache_read_tokens * p.cache_read_per_million_usd / 1_000_000
    if cache_write_tokens and p.cache_write_per_million_usd is not None:
        cost += cache_write_tokens * p.cache_write_per_million_usd / 1_000_000
    return cost


def snapshot_metadata() -> dict:
    """Return the frozen snapshot block from prices.json. Carried into result
    JSONL for traceability — any reader of the results can verify the rates."""
    with PRICES_PATH.open() as f:
        data = json.load(f)
    return data["snapshot"]
