"""W2-b: critique-revise debate, CrewAI, GPT-4.1-mini.

See `_shared/debate_base.py` for the base implementation.
"""

from __future__ import annotations

from ._shared.debate_base import DebateWorkload


class W2DebateCrewAI(DebateWorkload):
    id = "w2-debate-crewai-gpt-4-1-mini"
    framework = "crewai"


WORKLOAD = W2DebateCrewAI()
