"""W1-a: code-gen + assertion-test feedback, LangGraph, Haiku 4.5.

See `_shared/codegen_base.py` for the base implementation. This module
sets only the cell-identifying fields.
"""

from __future__ import annotations

from ._shared.codegen_base import CodegenWorkload


class W1CodegenLangGraph(CodegenWorkload):
    id = "w1-codegen-langgraph-claude-haiku-4-5"
    framework = "langgraph"


WORKLOAD = W1CodegenLangGraph()
