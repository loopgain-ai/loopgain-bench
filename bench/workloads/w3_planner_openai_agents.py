"""W3-a: planner-executor, OpenAI Agents SDK, GPT-4.1-mini.

See `_shared/planner_base.py` for the base implementation.
"""

from __future__ import annotations

from ._shared.planner_base import PlannerWorkload


class W3PlannerOpenAIAgents(PlannerWorkload):
    id = "w3-planner-openai-agents-gpt-4-1-mini"
    framework = "openai-agents"
    model = "gpt-4.1-mini"


WORKLOAD = W3PlannerOpenAIAgents()
