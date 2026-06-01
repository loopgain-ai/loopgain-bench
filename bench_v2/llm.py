"""LLM providers for Bench v2 — mock / openai / anthropic.

Default-safe: ``MockProvider`` makes zero network calls and returns scripted
SQL, so the whole pipeline (loop, oracle, metrics) validates at $0. Real
providers lazy-import their SDKs only when instantiated, and every call records
token usage so the runner can enforce a hard spend cap.

Frozen prices (USD per 1e6 tokens) match the v1 ``prices.json`` snapshot where
applicable; update deliberately for a real run.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Optional


def _with_retry(fn, attempts: int = 4, base: float = 2.0):
    """Call fn() with exponential backoff on transient API errors. Re-raises the
    last exception if all attempts fail (the runner records the task as failed)."""
    last = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            last = e
            if i < attempts - 1:
                time.sleep(base * (2 ** i))  # 2s, 4s, 8s
    raise last

PRICES = {
    # model: (input_per_million, output_per_million)
    "gpt-4.1-mini": (0.40, 1.60),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-sonnet-4-5": (3.00, 15.00),
    "mock": (0.0, 0.0),
}


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0

    def add(self, i: int, o: int) -> None:
        self.input_tokens += i
        self.output_tokens += o
        self.calls += 1

    def cost_usd(self, model: str) -> float:
        pin, pout = PRICES.get(model, (0.0, 0.0))
        return (self.input_tokens * pin + self.output_tokens * pout) / 1_000_000


@dataclass
class Provider:
    model: str
    usage: Usage = field(default_factory=Usage)

    def complete(self, system: str, user: str) -> str:  # pragma: no cover - interface
        raise NotImplementedError


_SQL_FENCE = re.compile(r"```(?:sql)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_sql(text: str) -> str:
    """Pull a SQL statement out of a model response (fenced block or raw)."""
    m = _SQL_FENCE.search(text)
    if m:
        return m.group(1).strip().rstrip(";").strip()
    # fall back: first line starting with a SQL verb through the end
    m2 = re.search(r"(SELECT|WITH)\b.*", text, re.DOTALL | re.IGNORECASE)
    return (m2.group(0).strip().rstrip(";").strip() if m2 else text.strip())


# --------------------------------------------------------------------------
# Mock provider — scripted, deterministic, $0
# --------------------------------------------------------------------------
class MockProvider(Provider):
    """Returns scripted responses keyed by an externally-incremented step.

    The runner/self-test supplies, per task, an ordered list of SQL strings (one
    per iteration). ``complete`` ignores the prompt and returns the next scripted
    item. Token counts are faked from string length so the cost path is exercised
    (priced at $0).
    """

    def __init__(self, model: str = "mock"):
        super().__init__(model=model)
        self._script: list[str] = []
        self._i = 0

    def load_script(self, sqls: list[str]) -> None:
        self._script = list(sqls)
        self._i = 0

    def complete(self, system: str, user: str) -> str:
        if not self._script:
            sql = "SELECT 1"  # no script loaded: benign default for a bare pipeline smoke
        elif self._i < len(self._script):
            sql = self._script[self._i]
        else:
            sql = self._script[-1]
        self._i += 1
        self.usage.add(max(1, len(system + user) // 4), max(1, len(sql) // 4))
        return f"```sql\n{sql}\n```"


# --------------------------------------------------------------------------
# Real providers — lazy imports; only used on an explicit real run
# --------------------------------------------------------------------------
class OpenAIProvider(Provider):
    def __init__(self, model: str = "gpt-4.1-mini"):
        super().__init__(model=model)
        from openai import OpenAI  # lazy

        self._client = OpenAI()

    def complete(self, system: str, user: str) -> str:
        resp = _with_retry(lambda: self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            temperature=0.0,
        ))
        u = resp.usage
        self.usage.add(getattr(u, "prompt_tokens", 0), getattr(u, "completion_tokens", 0))
        return resp.choices[0].message.content or ""


class AnthropicProvider(Provider):
    def __init__(self, model: str = "claude-sonnet-4-6"):
        super().__init__(model=model)
        from anthropic import Anthropic  # lazy

        self._client = Anthropic()

    def complete(self, system: str, user: str) -> str:
        resp = _with_retry(lambda: self._client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=system,
            messages=[{"role": "user", "content": user}],
        ))
        u = resp.usage
        self.usage.add(getattr(u, "input_tokens", 0), getattr(u, "output_tokens", 0))
        return "".join(getattr(b, "text", "") for b in resp.content)


def make_provider(kind: str, model: Optional[str] = None) -> Provider:
    kind = kind.lower()
    if kind == "mock":
        return MockProvider()
    if kind == "openai":
        return OpenAIProvider(model or "gpt-4.1-mini")
    if kind == "anthropic":
        return AnthropicProvider(model or "claude-sonnet-4-6")
    raise ValueError(f"unknown provider {kind!r} (mock|openai|anthropic)")
