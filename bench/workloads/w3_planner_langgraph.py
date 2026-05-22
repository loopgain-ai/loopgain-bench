"""W3-b: planner-executor, LangGraph, Claude Sonnet 4.6.

See `_shared/planner_base.py` for the base implementation. Per BENCH_PROTOCOL.md
§"Models" Table, W3-b uses Sonnet 4.6 — exercises cross-model parity within
the planner task family.
"""

from __future__ import annotations

from ._shared.planner_base import PlannerWorkload


class W3PlannerLangGraph(PlannerWorkload):
    id = "w3-planner-langgraph-claude-sonnet-4-6"
    framework = "langgraph"
    model = "claude-sonnet-4-6"


WORKLOAD = W3PlannerLangGraph()
