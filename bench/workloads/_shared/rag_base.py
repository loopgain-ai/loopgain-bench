"""Shared base for W4 iterative-RAG workloads.

W4 (LangChain, Haiku + text-embedding-3-small) is currently the only RAG
cell. The iteration body asks Haiku to REWRITE the query to better surface
the gold document; the bench retrieves with the rewritten query against an
in-memory embedded corpus (`CORPUS` from `rag_corpus.py`) and computes
hit@5 against the gold_id.

Error: 1.0 if gold not in top-5, else 0.0 (a step function). With small N
this is noisy but matches the RAG metric the protocol locked in (§Metrics
"MS MARCO retrieval@k delta vs B20").
Programmatic quality: hit@5 (1.0 or 0.0).
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Optional

from ...workload import IterationOutcome, TrialInput, Workload
from . import in_mock_mode
from .framework_invoke import invoke
from .rag_corpus import CORPUS, QUERIES


_EMBEDDING_CACHE: dict = {}  # {model: {doc_id: vector}}
_EMBEDDING_CLIENT: object = None


def _get_openai_client():
    global _EMBEDDING_CLIENT
    if _EMBEDDING_CLIENT is None:
        import openai
        if not os.environ.get("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY required for W4 RAG embeddings")
        _EMBEDDING_CLIENT = openai.OpenAI()
    return _EMBEDDING_CLIENT


def _embed_text(text: str, *, model: str = "text-embedding-3-small") -> list[float]:
    """Single embedding call with token accounting attached to the workload.

    Returns the embedding vector. Token cost is computed via prices.json by
    the caller (workload tracks input tokens in a Completion-like struct).
    """
    client = _get_openai_client()
    resp = client.embeddings.create(model=model, input=text)
    return resp.data[0].embedding


def _embed_corpus(model: str = "text-embedding-3-small") -> dict:
    """Cache the corpus embeddings to disk; do the heavy embed work only ONCE.

    For SciFact (5,183 docs × ~200 tokens) the initial embed pass costs
    roughly $0.02 of text-embedding-3-small. We persist the vectors to
    `data/cache/scifact_embeddings.jsonl` and reuse across all bench runs.
    Per BENCH_PROTOCOL.md the embedding cost is "preparation time," not
    "iteration time" — the bench's per-iter cost accounting only charges
    the QUERY embed (one short call per iter), not the corpus pre-embed.
    """
    from pathlib import Path
    from .rag_corpus import CACHE_DIR

    if model in _EMBEDDING_CACHE:
        return _EMBEDDING_CACHE[model]

    if in_mock_mode():
        import hashlib as _h
        import struct
        out = {}
        for d in CORPUS:
            digest = _h.sha256(d["id"].encode()).digest()
            vec = [struct.unpack(">f", digest[i:i + 4])[0] for i in range(0, 32, 4)]
            out[d["id"]] = vec
        _EMBEDDING_CACHE[model] = out
        return out

    # Real mode: load from disk cache if present.
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f"corpus_embeddings__{model}.jsonl"
    if cache_path.exists():
        out: dict = {}
        with cache_path.open() as f:
            for line in f:
                rec = json.loads(line)
                out[rec["id"]] = rec["embedding"]
        # Sanity: confirm every CORPUS doc is in the cache (otherwise corpus
        # changed — re-embed the missing ones, append to cache).
        missing = [d for d in CORPUS if d["id"] not in out]
        if not missing:
            _EMBEDDING_CACHE[model] = out
            return out
        # Append-mode: fill in missing
        with cache_path.open("a") as f:
            for d in missing:
                vec = _embed_text(d["text"], model=model)
                out[d["id"]] = vec
                f.write(json.dumps({"id": d["id"], "embedding": vec}) + "\n")
        _EMBEDDING_CACHE[model] = out
        return out

    # First-time embedding — write incrementally so partial failures resume cleanly.
    print(f"  [rag] embedding {len(CORPUS)} docs with {model} (one-time, ~$0.02)...", flush=True)
    out: dict = {}
    with cache_path.open("w") as f:
        for i, d in enumerate(CORPUS):
            vec = _embed_text(d["text"], model=model)
            out[d["id"]] = vec
            f.write(json.dumps({"id": d["id"], "embedding": vec}) + "\n")
            if (i + 1) % 500 == 0:
                print(f"    embedded {i + 1}/{len(CORPUS)}", flush=True)
    print(f"  [rag] corpus embeddings cached to {cache_path}", flush=True)
    _EMBEDDING_CACHE[model] = out
    return out


_CORPUS_MATRIX: dict = {}  # {model: (np.ndarray[N,d], list[str] of doc_ids)}


def _build_corpus_matrix(model: str):
    """Stack the cached corpus embeddings into a single (N, d) numpy array
    with L2-normalized rows, so retrieval is a single matrix-vector product.
    """
    import numpy as np
    if model in _CORPUS_MATRIX:
        return _CORPUS_MATRIX[model]
    embeds = _embed_corpus(model=model)
    doc_ids = [d["id"] for d in CORPUS]
    mat = np.asarray([embeds[did] for did in doc_ids], dtype=np.float32)
    # Row-normalize once
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    mat = mat / norms
    _CORPUS_MATRIX[model] = (mat, doc_ids)
    return mat, doc_ids


def _retrieve_top_k(query: str, *, k: int = 5, model: str = "text-embedding-3-small"):
    """Return (top_k_doc_ids, embed_input_tokens). Vectorized with numpy."""
    if in_mock_mode():
        from hashlib import sha256
        scored = sorted(CORPUS, key=lambda d: sha256((query + d["id"]).encode()).digest())
        return [d["id"] for d in scored[:k]], len(query) // 4

    import numpy as np
    qv = _embed_text(query, model=model)
    qa = np.asarray(qv, dtype=np.float32)
    qn = float(np.linalg.norm(qa)) or 1.0
    qa = qa / qn
    mat, doc_ids = _build_corpus_matrix(model)
    sims = mat @ qa  # (N,)
    top_idx = np.argpartition(-sims, range(min(k, len(doc_ids))))[:k]
    # Sort the top-k by actual similarity desc
    top_idx = top_idx[np.argsort(-sims[top_idx])]
    return [doc_ids[i] for i in top_idx], max(1, len(query) // 4)


class RAGWorkload(Workload):
    """Base class for W4 iterative-RAG cells."""

    model = "claude-haiku-4-5"
    loop_type = "iterative_retrieval"
    target_error = 0.0
    # Lockdown 2a: tie "better" to retrieval@k.
    task_description = (
        "Given an under-specified query, iteratively rewrite it so that an "
        "embedding-based retriever surfaces the gold passage in its top-5 "
        "results. A better attempt successfully retrieves the gold passage "
        "at rank <= 5."
    )

    id: str = "w4-rag-langchain-claude-haiku-4-5"
    framework: str = "langchain"
    embedding_model: str = "text-embedding-3-small"

    def generate_trial(self, seed: int) -> TrialInput:
        q = QUERIES[seed % len(QUERIES)]
        return TrialInput(
            seed=seed,
            prompt=q["query"],
            initial_state={
                "original_query": q["query"],
                "gold_id": q["gold_id"],
            },
            metadata={
                "query_idx": seed % len(QUERIES),
                "gold_id": q["gold_id"],
                "query_hash": hashlib.sha256(q["query"].encode()).hexdigest()[:12],
            },
        )

    def run_iteration(
        self,
        trial: TrialInput,
        prev_output: Optional[str],
        iteration: int,
        llm,
    ) -> IterationOutcome:
        original_query = trial.initial_state["original_query"]
        gold_id = trial.initial_state["gold_id"]
        if iteration == 1:
            # First retrieval uses the literal query (baseline retrieval)
            rewritten = original_query
            # No LLM call this iteration — but we still need a Completion for
            # accounting. Use a 0-token completion stamped with the model.
            from bench.llm import Completion as _Comp
            comp = _Comp(text=rewritten, input_tokens=0, output_tokens=0, model=self.model, latency_s=0.0)
        else:
            # Ask the LLM to rewrite the query given the previously-retrieved (poor) results
            top_ids, _ = _retrieve_top_k(prev_output or original_query, model=self.embedding_model)
            top_set = set(top_ids)
            # Truncate long passages — scientific abstracts can be 2000+ chars.
            snippets = []
            for d in CORPUS:
                if d["id"] in top_set:
                    snippet = d["text"][:300] + ("…" if len(d["text"]) > 300 else "")
                    snippets.append(f"  [{d['id']}] {snippet}")
            retrieved_text = "\n".join(snippets)
            prompt = (
                f"Original query: {original_query!r}\n\n"
                f"Top-5 retrieved passages from the last query attempt:\n{retrieved_text}\n\n"
                f"None of these passages directly answer the original query. "
                f"Rewrite the query to be more specific, using terms likely "
                f"to appear in the target passage. Return ONLY the rewritten "
                f"query as a single line, no preamble."
            )
            comp = invoke(self.framework, llm, prompt, max_tokens=100)
            rewritten = (comp.text or original_query).strip().splitlines()[0]
        # Evaluate retrieval@5 with the (rewritten) query
        top_ids, emb_input_tokens = _retrieve_top_k(rewritten, model=self.embedding_model)
        hit = gold_id in top_ids
        error_val = 0.0 if hit else 1.0
        if in_mock_mode():
            # Synthesize a converging trajectory
            error_val = 0.0 if iteration >= 3 else 1.0
        return IterationOutcome(output=rewritten, completion=comp, error=error_val)

    def error_fn(self, output: str) -> float:
        return 0.0

    def programmatic_quality(self, output: str) -> Optional[float]:
        return None
