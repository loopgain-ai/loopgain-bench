"""W2-a: critique-revise debate, AutoGen v0.4+, GPT-4.1-mini.

See `_shared/debate_base.py` for the base implementation.
"""

from __future__ import annotations

from ._shared.debate_base import DebateWorkload


class W2DebateAutoGen(DebateWorkload):
    id = "w2-debate-autogen-gpt-4-1-mini"
    framework = "autogen"


WORKLOAD = W2DebateAutoGen()
