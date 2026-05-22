"""W4 RAG corpus: BEIR/scifact (Wadden et al., 2020).

Per BENCH_PROTOCOL.md Amendment 2026-05-21g (pre-data, scenario-design class),
W4 was migrated from a 30-doc inline corpus to BEIR/scifact after the inline
corpus failed the density check at 0% iter-1 failure rate. SciFact is a
published, peer-reviewed retrieval benchmark with:

  - 5,183 corpus documents (PubMed abstracts on scientific claims)
  - 1,109 queries (terse scientific claim statements)
  - 339 test qrels (query → relevant doc mappings with relevance scores)

The bench uses the **test qrels** as the W4 query set, giving 339 trials of
"can the retriever find the gold abstract for this scientific claim?"

Implementation discipline:
  - Corpus embeddings are precomputed ONCE and cached to disk at
    `data/cache/scifact_embeddings.jsonl` (~$0.02 of text-embedding-3-small
    on a one-time basis; reused across all bench runs).
  - In mock mode (BENCH_MOCK=1), embeddings are synthesized deterministically
    so the harness smoke runs without network/billing.

The legacy 30-doc inline corpus is preserved at the bottom of this module
under `_LEGACY_INLINE_CORPUS`/`_LEGACY_INLINE_QUERIES` for reference and to
keep mock tests cheap.
"""

from __future__ import annotations

import hashlib
import json
import os
import struct
from functools import lru_cache
from pathlib import Path
from typing import Optional

CACHE_DIR = Path(__file__).resolve().parents[3] / "data" / "cache"


@lru_cache(maxsize=1)
def _load_scifact() -> tuple[list[dict], list[dict]]:
    """Load (CORPUS, QUERIES) from BEIR/scifact via HuggingFace datasets.

    Returns:
        CORPUS: [{"id": str, "text": str}, ...]
        QUERIES: [{"query": str, "gold_id": str}, ...]

    QUERIES are derived from the test qrels: each entry pairs the query's
    text with one of its relevant corpus ids (if a query has multiple gold
    docs, the bench picks the first by qrel score then by corpus-id order).
    """
    from datasets import load_dataset

    corpus_ds = load_dataset("BeIR/scifact", "corpus", split="corpus")
    queries_ds = load_dataset("BeIR/scifact", "queries", split="queries")
    qrels_ds = load_dataset("BeIR/scifact-qrels", split="test")

    # Corpus: keep id + (title + text) so embeddings have full context
    corpus = []
    for row in corpus_ds:
        text = row["title"] + " " + row["text"] if row.get("title") else row["text"]
        corpus.append({"id": str(row["_id"]), "text": text})

    # Map query_id -> query text
    q_by_id: dict[str, str] = {str(r["_id"]): r["text"] for r in queries_ds}

    # Build queries from qrels (test). One row per (query, gold_id) pair.
    qrels_rows = sorted(
        [(str(r["query-id"]), str(r["corpus-id"]), int(r["score"])) for r in qrels_ds],
        key=lambda t: (t[0], -t[2], t[1]),
    )
    # Take first qrel per query_id (highest score, then lowest corpus_id)
    seen: set[str] = set()
    queries: list[dict] = []
    for qid, cid, score in qrels_rows:
        if qid in seen or qid not in q_by_id:
            continue
        seen.add(qid)
        queries.append({"query": q_by_id[qid], "gold_id": cid, "qrel_score": score})

    return corpus, queries


def _ensure_scifact_loaded():
    """Lazy load — in mock mode short-circuit to the legacy inline corpus
    so harness tests don't require HuggingFace dataset cache."""
    if os.environ.get("BENCH_MOCK") == "1" and os.environ.get("BENCH_USE_REAL_CORPUS") != "1":
        return _LEGACY_INLINE_CORPUS, _LEGACY_INLINE_QUERIES
    return _load_scifact()


# Module-level CORPUS / QUERIES — lazy via property-like proxy so the
# 5,183-doc load only happens when actually needed.
class _LazyList:
    def __init__(self, getter, idx: int):
        self._getter = getter
        self._idx = idx
        self._cache = None

    def _materialize(self):
        if self._cache is None:
            self._cache = self._getter()[self._idx]
        return self._cache

    def __len__(self):
        return len(self._materialize())

    def __getitem__(self, k):
        return self._materialize()[k]

    def __iter__(self):
        return iter(self._materialize())


CORPUS = _LazyList(_ensure_scifact_loaded, 0)
QUERIES = _LazyList(_ensure_scifact_loaded, 1)


def get_query(seed: int) -> dict:
    return QUERIES[seed % len(QUERIES)]


# Mock-mode legacy corpus — kept for harness tests that must not hit network.
_LEGACY_INLINE_CORPUS = [
    {"id": "d01", "text": "The Trans-Canada Highway is the longest national highway in the world, spanning 7,821 kilometres from St. John's, Newfoundland to Victoria, British Columbia."},
    {"id": "d02", "text": "K2-18b is a sub-Neptune exoplanet 124 light-years from Earth in the constellation Leo, orbiting within the habitable zone of its red dwarf star."},
    {"id": "d03", "text": "The James Webb Space Telescope launched on December 25, 2021, with primary mirror diameter 6.5 metres and sun-shield the size of a tennis court."},
    {"id": "d04", "text": "Bitcoin's halving on April 19, 2024 reduced the block reward from 6.25 BTC to 3.125 BTC, the fourth such event in the network's history."},
    {"id": "d05", "text": "Wood frogs can survive freezing by producing glucose as a cryoprotectant; up to 65% of their body water becomes ice."},
    {"id": "d06", "text": "The Indus Valley Civilization, c. 2500-1900 BCE, had cities like Harappa and Mohenjo-Daro with grid-pattern streets and sophisticated drainage."},
    {"id": "d07", "text": "Quantum tunneling, predicted by Hund in 1927, lets particles pass through energy barriers and underlies alpha decay and flash memory."},
    {"id": "d08", "text": "The Anaconda Plan, devised by Union General Winfield Scott in 1861, proposed blockading Southern ports to defeat the Confederacy."},
    {"id": "d09", "text": "Carbonic anhydrase, an enzyme abundant in red blood cells, catalyzes the interconversion of CO2 and water with bicarbonate."},
    {"id": "d10", "text": "The Antikythera mechanism, recovered from a Greek shipwreck dated to 60 BCE, is an ancient analog computer for astronomical predictions."},
    {"id": "d11", "text": "The Mariana Trench reaches 10,984 metres at the Challenger Deep, the deepest known oceanic trench."},
    {"id": "d12", "text": "Voyager 1 crossed the heliopause on August 25, 2012, becoming the first human-made object in interstellar space."},
    {"id": "d13", "text": "The Burgess Shale, discovered in 1909 by Charles Walcott, preserves soft-bodied Cambrian fauna including the predator Anomalocaris."},
    {"id": "d14", "text": "Erlang, developed by Joe Armstrong at Ericsson in 1986, was designed for telecom switches and pioneered actor-model concurrency."},
    {"id": "d15", "text": "The Treaty of Westphalia in 1648 ended the Thirty Years' War and established the modern principle of state sovereignty."},
]

_LEGACY_INLINE_QUERIES = [
    {"query": "long road across a country", "gold_id": "d01"},
    {"query": "planet beyond solar system", "gold_id": "d02"},
    {"query": "telescope launched recently", "gold_id": "d03"},
    {"query": "crypto event reducing supply", "gold_id": "d04"},
    {"query": "frogs and cold weather", "gold_id": "d05"},
    {"query": "old city with drains", "gold_id": "d06"},
    {"query": "particles passing through barriers", "gold_id": "d07"},
    {"query": "civil war military strategy", "gold_id": "d08"},
    {"query": "blood and gas exchange", "gold_id": "d09"},
    {"query": "ancient mechanical device", "gold_id": "d10"},
    {"query": "deepest part of the ocean", "gold_id": "d11"},
    {"query": "spacecraft leaving solar system", "gold_id": "d12"},
    {"query": "old Canadian fossils", "gold_id": "d13"},
    {"query": "concurrent programming language for phones", "gold_id": "d14"},
    {"query": "treaty ending a long war", "gold_id": "d15"},
]
