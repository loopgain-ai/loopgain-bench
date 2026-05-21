"""LLM clients for the bench: real Anthropic, real OpenAI, and a Mock.

Real clients are thin wrappers that return a `Completion` dataclass with the
token-accounting fields the bench needs. The bench DOES NOT use streaming —
we want full token accounting on every call.

The Mock client is for harness smoke-testing without API calls. It
deterministically produces error trajectories based on (seed, scenario) — see
`MockLLMClient` docstring. **Mock mode is NEVER used in registered results;
only for `make mock` and `tests/`.**
"""

from __future__ import annotations

import math
import os
import random
import time
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Completion:
    """One LLM call's output. Token counts are real provider-reported numbers.

    For real clients: input_tokens / output_tokens come from the API response's
    usage block. For mock: synthesized (declared on construction).
    """

    text: str
    input_tokens: int
    output_tokens: int
    model: str
    latency_s: float


class RealAnthropic:
    """Thin wrapper over the anthropic SDK. Returns Completion."""

    def __init__(self, model: str):
        import anthropic  # noqa: PLC0415 — lazy so the bench imports without SDK installed
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Required for real-API runs."
            )
        self._client = anthropic.Anthropic()
        self.model = model

    def call(self, prompt: str, *, system: Optional[str] = None, max_tokens: int = 1024) -> Completion:
        t0 = time.time()
        kwargs = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system
        msg = self._client.messages.create(**kwargs)
        text = "\n".join(b.text for b in msg.content if getattr(b, "type", None) == "text").strip()
        return Completion(
            text=text,
            input_tokens=msg.usage.input_tokens,
            output_tokens=msg.usage.output_tokens,
            model=self.model,
            latency_s=time.time() - t0,
        )


class RealOpenAI:
    """Thin wrapper over the openai SDK."""

    def __init__(self, model: str):
        import openai  # noqa: PLC0415
        if not os.environ.get("OPENAI_API_KEY"):
            raise RuntimeError(
                "OPENAI_API_KEY is not set. Required for real-API runs."
            )
        self._client = openai.OpenAI()
        self.model = model

    def call(self, prompt: str, *, system: Optional[str] = None, max_tokens: int = 1024) -> Completion:
        t0 = time.time()
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=max_tokens,
        )
        text = (resp.choices[0].message.content or "").strip()
        usage = resp.usage
        return Completion(
            text=text,
            input_tokens=usage.prompt_tokens,
            output_tokens=usage.completion_tokens,
            model=self.model,
            latency_s=time.time() - t0,
        )


class MockLLMClient:
    """Deterministic LLM mock for harness smoke-testing.

    The mock is parameterized by a `scenario` string that controls the
    error trajectory shape:

        - "fast_converge" — error drops to 0 in 1-2 iters
        - "converging"    — geometric decline over many iters
        - "stalling"      — random walk near initial error
        - "oscillating"   — symmetric alternation around fixed point
        - "diverging"     — error grows iteration over iteration

    The mock does NOT model an LLM's text output — it returns synthetic
    text strings of controlled length, so that token accounting is
    deterministic and cost calculations exercise correctly. The
    bench harness uses these to verify pipes (paired runs, JSONL writes,
    LoopGain integration) without spending real API budget.

    Real workloads provide their OWN response-text generation; the mock is
    only used by `bench.runner --mock`, which bypasses workload error
    functions and feeds error values directly to LoopGain. See
    `bench/runner.py`.
    """

    def __init__(self, scenario: str, seed: int, model: str = "mock-haiku"):
        self.scenario = scenario
        self.seed = seed
        self.model = model
        self._rng = random.Random(seed)
        self._iter = 0
        self._initial_error = 5.0 + self._rng.random() * 3.0  # 5.0–8.0

    def synth_error(self, iteration: int) -> float:
        """Return the synthetic error for a given (1-indexed) iteration."""
        E0 = self._initial_error
        noise = self._rng.gauss(0, 0.1 * E0)
        if self.scenario == "fast_converge":
            return max(0.0, 0.01 if iteration >= 1 else E0)
        if self.scenario == "converging":
            return max(0.0, E0 * (0.7 ** (iteration - 1)) + noise)
        if self.scenario == "stalling":
            return max(0.0, E0 + noise)
        if self.scenario == "oscillating":
            return max(0.0, E0 * (1.0 + 0.5 * math.sin(math.pi * iteration)) + noise)
        if self.scenario == "diverging":
            return max(0.0, E0 * (1.2 ** (iteration - 1)) + noise)
        raise ValueError(f"unknown scenario {self.scenario!r}")

    def call(self, prompt: str, *, system: Optional[str] = None, max_tokens: int = 1024) -> Completion:
        # Token counts and text are synthetic but deterministic given (seed, iter).
        self._iter += 1
        text_len = 200 + self._rng.randint(0, 50)
        return Completion(
            text=f"[mock {self.scenario} iter={self._iter} seed={self.seed}]",
            input_tokens=len(prompt) // 4,
            output_tokens=text_len // 4,
            model=self.model,
            latency_s=0.001,
        )


def client_for_model(model: str, scenario: Optional[str] = None, seed: int = 0):
    """Factory: real client for real models; mock if BENCH_MOCK=1 or scenario set."""
    if os.environ.get("BENCH_MOCK") == "1" or scenario is not None:
        return MockLLMClient(scenario=scenario or "converging", seed=seed, model=model)
    if model.startswith("claude-"):
        return RealAnthropic(model)
    if model.startswith("gpt-") or model.startswith("o"):
        return RealOpenAI(model)
    raise ValueError(f"no client mapping for model {model!r}")
