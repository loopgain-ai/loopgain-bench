"""Workload 5 — Adversarial (LangGraph variant).

Same corpus and same fact-counting error function as `w5_adversarial`.
The per-iteration revise step is executed via a one-node LangGraph
StateGraph (see `_shared/framework_invoke.py`), so the "framework=langgraph"
tag is genuinely earned. Methodology disclosure in that helper file.
"""

from __future__ import annotations

from typing import Optional

from ..workload import IterationOutcome
from ._shared.framework_invoke import invoke
from .w5_adversarial import W5Adversarial


class W5AdversarialLangGraph(W5Adversarial):
    id = "w5-adversarial-langgraph-claude-haiku-4-5"
    framework = "langgraph"

    def run_iteration(
        self,
        trial,
        prev_output: Optional[str],
        iteration: int,
        llm,
    ) -> IterationOutcome:
        if iteration == 1:
            prompt = (
                f"Rewrite this passage to be 40 words long. Return only the "
                f"rewritten passage, no preamble.\n\n{trial.prompt}"
            )
        else:
            prompt = (
                f"Make this even shorter. Return only the rewritten passage, "
                f"no preamble.\n\n{prev_output}"
            )
        comp = invoke(self.framework, llm, prompt, max_tokens=400)
        text = (comp.text or prev_output) or trial.prompt
        return IterationOutcome(
            output=text,
            completion=comp,
            error=self.error_fn(text, facts=trial.initial_state["facts"]),
        )


WORKLOAD = W5AdversarialLangGraph()
