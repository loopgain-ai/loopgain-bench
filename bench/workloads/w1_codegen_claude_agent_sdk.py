"""W1-b: code-gen + assertion-test feedback, Claude Agent SDK, Haiku 4.5.

See `_shared/codegen_base.py` for the base implementation.
"""

from __future__ import annotations

from ._shared.codegen_base import CodegenWorkload


class W1CodegenClaudeAgentSDK(CodegenWorkload):
    id = "w1-codegen-claude-agent-sdk-claude-haiku-4-5"
    framework = "claude-agent-sdk"


WORKLOAD = W1CodegenClaudeAgentSDK()
