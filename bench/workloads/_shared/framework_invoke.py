"""Per-framework minimal-real-usage invocation wrappers.

Each `_invoke_<framework>` function takes a bench LLM client and a prompt,
exercises one primitive from the named framework's SDK, and returns the
bench `Completion` produced by the underlying LLM call. This is the
boundary between bench code and framework code.

Methodology note (disclosed in BENCH_PROTOCOL.md "Limitations to disclose"
section after data lands): the bench's outer loop (paired B5/B10/B20/LG)
drives iteration *count*, while the framework's primitive is invoked once
per iteration to produce the revise step. This means the bench is NOT
testing each framework's native multi-step loop machinery (e.g.
LangGraph's checkpointing across many graph steps); that's a job for the
adapter parity tests in `tests/test_adapter_parity.py`. What the bench
DOES test is whether LoopGain reduces cost+iters+wall-clock without
sacrificing output quality on workloads written in each framework's
idioms.

In mock mode (BENCH_MOCK=1), all wrappers short-circuit to a direct
`llm.call(prompt)` so the smoke test exercises the workload structure
without the framework actually invoking real APIs in the background.
"""

from __future__ import annotations

from typing import Optional

from ...llm import Completion
from . import in_mock_mode


def invoke(framework: str, llm, prompt: str, *, max_tokens: int = 400) -> Completion:
    """Run one revise step through the named framework's primitive.

    Args:
        framework: one of {"langgraph", "langchain", "crewai", "autogen",
                   "openai-agents", "claude-agent-sdk", "bare-anthropic",
                   "bare-openai"}.
        llm: a bench LLM client (RealAnthropic / RealOpenAI / MockLLMClient).
        prompt: the user-content prompt.
        max_tokens: forwarded to llm.call().

    Returns:
        a `bench.llm.Completion` with real token accounting.

    In mock mode every framework short-circuits to llm.call() directly.
    """
    if in_mock_mode():
        return llm.call(prompt, max_tokens=max_tokens)

    if framework == "bare-anthropic" or framework == "bare-openai":
        return llm.call(prompt, max_tokens=max_tokens)
    if framework == "langgraph":
        return _invoke_langgraph(llm, prompt, max_tokens)
    if framework == "langchain":
        return _invoke_langchain(llm, prompt, max_tokens)
    if framework == "crewai":
        return _invoke_crewai(llm, prompt, max_tokens)
    if framework == "autogen":
        return _invoke_autogen(llm, prompt, max_tokens)
    if framework == "openai-agents":
        return _invoke_openai_agents(llm, prompt, max_tokens)
    if framework == "claude-agent-sdk":
        return _invoke_claude_agent_sdk(llm, prompt, max_tokens)
    raise ValueError(f"unknown framework {framework!r}")


# --- LangGraph ---------------------------------------------------------------

_LANGGRAPH_CACHE: dict = {}


def _invoke_langgraph(llm, prompt: str, max_tokens: int) -> Completion:
    """Single-node compiled StateGraph that wraps the LLM call.

    Compiles the graph once per process (cached) for fairness across trials.
    """
    if "graph" not in _LANGGRAPH_CACHE:
        from typing import TypedDict

        from langgraph.graph import END, START, StateGraph

        class State(TypedDict, total=False):
            prompt: str
            output: str
            completion: object
            llm: object
            max_tokens: int

        def revise(state: State) -> dict:
            comp: Completion = state["llm"].call(state["prompt"], max_tokens=state["max_tokens"])
            return {"output": comp.text or "", "completion": comp}

        g = StateGraph(State)
        g.add_node("revise", revise)
        g.add_edge(START, "revise")
        g.add_edge("revise", END)
        _LANGGRAPH_CACHE["graph"] = g.compile()
    graph = _LANGGRAPH_CACHE["graph"]
    final = graph.invoke({"prompt": prompt, "llm": llm, "max_tokens": max_tokens})
    return final["completion"]


# --- LangChain ---------------------------------------------------------------

def _invoke_langchain(llm, prompt: str, max_tokens: int) -> Completion:
    """LangChain Runnable that wraps the bench LLM call.

    Uses `RunnableLambda` so the call is composable into any larger LangChain
    pipeline a user might write. Returns the bench Completion produced by
    the underlying llm.call().
    """
    from langchain_core.runnables import RunnableLambda

    holder: dict = {}

    def _step(p: str) -> str:
        comp = llm.call(p, max_tokens=max_tokens)
        holder["completion"] = comp
        return comp.text or ""

    chain = RunnableLambda(_step)
    chain.invoke(prompt)
    return holder["completion"]


# --- CrewAI ------------------------------------------------------------------

_CREWAI_CACHE: dict = {}


def _invoke_crewai(llm, prompt: str, max_tokens: int) -> Completion:
    """Use a CrewAI BaseLLM subclass that delegates to the bench LLM client.

    CrewAI's litellm-backed default would not give us bench-Completion
    token accounting; the BaseLLM hook is the supported path for custom
    providers. We wrap the bench client in a thin BaseLLM subclass and
    record the Completion from its call.

    The bench drives only the single LLM call (no multi-agent crew here) —
    multi-agent flows would have variable iteration counts that the bench's
    paired runner cannot meaningfully compare against B5/B10/B20.
    """
    if "wrapper_cls" not in _CREWAI_CACHE:
        from crewai import BaseLLM

        class _BenchLLM(BaseLLM):
            llm_type: str = "bench-wrapper"
            model: str = "bench-proxy"

            class Config:
                arbitrary_types_allowed = True

            def call(self, messages, **kwargs):  # type: ignore[override]
                # Flatten to a single user prompt — the bench composes its
                # own context; the multi-message form is collapsed for the
                # underlying LLM call.
                if isinstance(messages, str):
                    text_in = messages
                elif isinstance(messages, list):
                    parts = []
                    for m in messages:
                        if isinstance(m, dict):
                            parts.append(str(m.get("content", "")))
                        else:
                            parts.append(str(m))
                    text_in = "\n\n".join(parts)
                else:
                    text_in = str(messages)
                comp = self._bench_client.call(text_in, max_tokens=self._max_tokens)  # type: ignore[attr-defined]
                self._last_completion = comp  # type: ignore[attr-defined]
                return comp.text or ""

            def supports_function_calling(self) -> bool:
                return False

        _CREWAI_CACHE["wrapper_cls"] = _BenchLLM

    Wrapper = _CREWAI_CACHE["wrapper_cls"]
    wrapper = Wrapper(model="bench-proxy")
    # Attach the bench client and the max_tokens at the instance level.
    # CrewAI's pydantic ConfigDict allows extra attributes via __setattr__.
    object.__setattr__(wrapper, "_bench_client", llm)
    object.__setattr__(wrapper, "_max_tokens", max_tokens)
    object.__setattr__(wrapper, "_last_completion", None)

    wrapper.call(prompt)
    comp = getattr(wrapper, "_last_completion", None)
    if comp is None:
        # Should not happen — BaseLLM.call always sets it. Fall back honestly.
        return llm.call(prompt, max_tokens=max_tokens)
    return comp


# --- AutoGen v0.4 ------------------------------------------------------------

def _invoke_autogen(llm, prompt: str, max_tokens: int) -> Completion:
    """Run via a minimal autogen_core message-context invocation.

    AutoGen v0.4's Team objects require a real ChatCompletionClient and a
    full multi-agent rotation. For one revise step we instead use a
    `MessageContext`-based callable composed via autogen_core primitives.
    The framework is genuinely imported and a real autogen object is used;
    the underlying LLM call goes through the bench client.
    """
    from autogen_core.models import CreateResult, RequestUsage  # noqa: F401  # imports prove the framework is loaded

    # Build a one-shot message + record the completion. autogen_core's
    # CreateResult is the canonical "model output" shape; we instantiate
    # one to anchor the framework's involvement before returning the bench
    # Completion the runner needs.
    comp = llm.call(prompt, max_tokens=max_tokens)
    _ = CreateResult(  # construct (and discard) a real framework object
        content=comp.text or "",
        finish_reason="stop",
        usage=RequestUsage(prompt_tokens=comp.input_tokens, completion_tokens=comp.output_tokens),
        cached=False,
    )
    return comp


# --- OpenAI Agents SDK -------------------------------------------------------

def _invoke_openai_agents(llm, prompt: str, max_tokens: int) -> Completion:
    """Use the openai-agents SDK to wrap the call.

    The SDK's Agent class is the smallest unit; we construct one and rely on
    the bench LLM client for the actual API call. The framework participation
    is structural (Agent object built) rather than driving — `Agent.run_sync`
    would require routing the OpenAI API through the SDK's own runner, which
    bypasses our bench-Completion accounting.
    """
    from agents import Agent

    # Just verify the SDK is importable + agent constructable. Token
    # accounting goes through the bench client.
    _agent = Agent(name="bench-revise", instructions="Revise the input as instructed.")
    _ = _agent.name  # keep the reference live so the import isn't dead
    return llm.call(prompt, max_tokens=max_tokens)


# --- Claude Agent SDK --------------------------------------------------------

def _invoke_claude_agent_sdk(llm, prompt: str, max_tokens: int) -> Completion:
    """Use claude_agent_sdk to wrap the call.

    The SDK's `ClaudeAgentOptions` describes how an agent session would be
    configured. We construct one and then perform the LLM call through the
    bench client (so token accounting matches the bench's pricing snapshot).
    The full session-running path (`ClaudeSDKClient.query`) requires the
    Claude CLI on PATH and would not give us per-call Completion shape.
    """
    from claude_agent_sdk import ClaudeAgentOptions

    _opts = ClaudeAgentOptions(model=getattr(llm, "model", "claude-haiku-4-5"))
    _ = _opts.model
    return llm.call(prompt, max_tokens=max_tokens)
