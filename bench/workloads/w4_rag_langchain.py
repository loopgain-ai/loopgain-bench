"""W4: iterative-retrieval RAG, LangChain, Haiku 4.5 + text-embedding-3-small.

See `_shared/rag_base.py` for the base implementation.
"""

from __future__ import annotations

from ._shared.rag_base import RAGWorkload


class W4RAGLangChain(RAGWorkload):
    id = "w4-rag-langchain-claude-haiku-4-5"
    framework = "langchain"


WORKLOAD = W4RAGLangChain()
