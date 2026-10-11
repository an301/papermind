"""The retrieval suite: recall@k of BM25, dense and hybrid on the benchmark questions. No API key.

Each question goes to the retriever as asked -- no agent query rewrite -- so
this measures the retriever alone; the agent suite measures recall after the
agent's rewrite (``runner.py``). The retriever is built exactly as the
retriever MCP server builds it (``build_retriever``), in-process.

Per-k calls, not one deep call sliced. A single arm ranks every chunk once, so
its top 5 is a prefix of its top 50 and one deep call serves every k. Hybrid
is different: each arm contributes its top ``max(4k, 20)`` to RRF, so a chunk
both arms rank 22nd changes the fused order at k=10 but not at k=5. Hybrid
recall@k therefore comes from a call with that k, as the agent would make it.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from evals.harness import scorers
from evals.harness.dataset import BenchmarkRow
from papermind.config import Settings, get_settings
from papermind.ingest.store import PaperStore
from papermind.retrieval.dense import EmbeddingCache, fingerprint, get_embedder
from papermind.retrieval.hybrid import HybridRetriever
from papermind.retrieval.text import indexable_text

DEFAULT_MODES: tuple[str, ...] = ("bm25", "dense", "hybrid")
DEFAULT_KS: tuple[int, ...] = (5, 10)
# How deep to look for the gold chunk when reporting its rank (diagnostics:
# "dense had it at 3, BM25 at 41").
ANALYSIS_DEPTH = 50
# Arms whose top k is a prefix of their full ranking; see the module docstring.
PREFIX_STABLE = frozenset({"bm25", "dense"})


@dataclass
class ModeResult:
    hits: dict[str, float]  # "recall@5" -> 0.0 / 1.0
    rank: int | None  # gold rank within the largest-k result (what recall@max_k sees)
    rank_deep: int | None  # gold rank within ANALYSIS_DEPTH; for hybrid, from a deeper fusion
    reciprocal_rank: float  # 1/rank within the largest-k result
    top: list[str]  # the largest-k result, best first


@dataclass
class RetrievalRowResult:
    id: str
    paper_id: str
    origin: str
    query_type: str
    level: str
    question: str
    gold_chunk: str
    modes: dict[str, ModeResult] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def flat(self) -> dict[str, Any]:
        """One table row: identifying fields plus "<mode> recall@k" / "<mode> rank" columns."""
        out: dict[str, Any] = {
            "id": self.id,
            "paper_id": self.paper_id,
            "origin": self.origin,
            "query_type": self.query_type,
            "level": self.level,
            "gold_chunk": self.gold_chunk,
        }
        for mode, result in self.modes.items():
            for name, value in result.hits.items():
                out[f"{mode} {name}"] = value
            out[f"{mode} rank"] = result.rank_deep
        return out


def build_retriever(settings: Settings | None = None) -> HybridRetriever:
    """The retriever MCP server's own construction, so the suite cannot drift from production."""
    from mcp_servers.retriever.server import build_retriever as server_build

    return server_build(settings or get_settings())


def missing_embeddings(paper_ids: Sequence[str], settings: Settings | None = None) -> list[str]:
    """Papers whose chunk vectors are not in the on-disk cache (computing them takes minutes on CPU).

    Checked without loading the model: the cache key is the model name,
    window settings and chunk texts.
    """
    settings = settings or get_settings()
    store = PaperStore(settings.papers_dir)
    cache = EmbeddingCache(settings.cache_dir / "embeddings")
    name = get_embedder().name
    missing: list[str] = []
    for paper_id in sorted(set(paper_ids)):
        record = store.load(paper_id)
        if record is None:
            missing.append(paper_id)
            continue
        key = fingerprint(name, [indexable_text(c) for c in record.chunks])
        if not cache.path(paper_id, key).exists():
            missing.append(paper_id)
    return missing


def model_downloaded(settings: Settings | None = None) -> bool:
    """Whether the embedding model is already on disk (fastembed downloads it otherwise)."""
    models = (settings or get_settings()).data_dir / "models"
    return models.is_dir() and any(p.name.startswith("models--") for p in models.iterdir())


async def _row(
    retriever: HybridRetriever, row: BenchmarkRow, modes: Sequence[str], ks: Sequence[int]
) -> RetrievalRowResult:
    paths = retriever.paths()
    out = RetrievalRowResult(
        id=row.id,
        paper_id=row.paper_id,
        origin=row.origin,
        query_type=row.query_type,
        level=row.target_level,
        question=row.question,
        gold_chunk=row.gold_chunk,
    )
    max_k = max(ks)
    for mode in modes:
        search = paths[mode]
        deep = [c.chunk_id for c in await search(row.question, row.paper_id, max(ANALYSIS_DEPTH, max_k))]
        by_k: dict[int, list[str]] = {}
        for k in ks:
            if mode in PREFIX_STABLE:
                by_k[k] = deep[:k]
            else:
                by_k[k] = [c.chunk_id for c in await search(row.question, row.paper_id, k)]
        top = by_k[max_k]
        out.modes[mode] = ModeResult(
            hits={f"recall@{k}": scorers.recall_at_k(row.gold_chunk, by_k[k], k) for k in ks},
            rank=scorers.gold_rank(row.gold_chunk, top),
            rank_deep=scorers.gold_rank(row.gold_chunk, deep),
            reciprocal_rank=scorers.reciprocal_rank(row.gold_chunk, top),
            top=top,
        )
    return out


async def run_retrieval(
    rows: Sequence[BenchmarkRow],
    *,
    modes: Sequence[str] = DEFAULT_MODES,
    ks: Sequence[int] = DEFAULT_KS,
    retriever: HybridRetriever | None = None,
    on_done: Callable[[RetrievalRowResult], None] | None = None,
) -> list[RetrievalRowResult]:
    """Every row through every mode. Sequential: the encoder is one locked model per process anyway."""
    retriever = retriever or build_retriever()
    unknown = sorted(set(modes) - set(retriever.paths()))
    if unknown:
        raise ValueError(f"unknown retrieval modes {unknown}; expected {sorted(retriever.paths())}")
    if not ks or min(ks) < 1:
        raise ValueError("ks must be positive integers")
    await asyncio.to_thread(retriever.registry.embedder.embed_query, "warm-up")
    results: list[RetrievalRowResult] = []
    for row in rows:
        result = await _row(retriever, row, modes, sorted(set(ks)))
        results.append(result)
        if on_done is not None:
            on_done(result)
    return results


def _cell(results: Sequence[RetrievalRowResult], mode: str, ks: Sequence[int]) -> dict[str, Any]:
    cell: dict[str, Any] = {"n": len(results)}
    for k in ks:
        cell[f"recall@{k}"] = scorers.mean(r.modes[mode].hits[f"recall@{k}"] for r in results)
    cell[f"mrr@{max(ks)}"] = scorers.mean(r.modes[mode].reciprocal_rank for r in results)
    return cell


def summarize_retrieval(
    results: Sequence[RetrievalRowResult], modes: Sequence[str], ks: Sequence[int]
) -> dict[str, Any]:
    ks = sorted(set(ks))

    def table(subset: Sequence[RetrievalRowResult]) -> dict[str, Any]:
        return {mode: _cell(subset, mode, ks) for mode in modes}

    def sliced(key: str) -> dict[str, Any]:
        groups: dict[str, list[RetrievalRowResult]] = {}
        for r in results:
            groups.setdefault(str(getattr(r, key)), []).append(r)
        order = [k for k in scorers.LEVEL_ORDER if k in groups] + sorted(
            k for k in groups if k not in scorers.LEVEL_ORDER
        )
        return {k: table(groups[k]) for k in order}

    summary: dict[str, Any] = {
        "n": len(results),
        "modes": list(modes),
        "ks": ks,
        "overall": table(results),
        "by_origin": sliced("origin"),
        "by_level": sliced("level"),
        "by_query_type": sliced("query_type"),
        "by_paper": sliced("paper_id"),
    }
    headline_mode = "hybrid" if "hybrid" in modes else modes[0]
    headline_k = 5 if 5 in ks else ks[0]
    summary["headline"] = {
        "metric": f"{headline_mode} recall@{headline_k}",
        "value": summary["overall"][headline_mode][f"recall@{headline_k}"] if results else None,
    }
    return summary


def timed_run(rows: Sequence[BenchmarkRow], **kwargs: Any) -> tuple[list[RetrievalRowResult], float]:
    """``run_retrieval`` from synchronous code (pytest fixtures, the CLI); returns results and seconds."""
    started = time.perf_counter()
    results = asyncio.run(run_retrieval(rows, **kwargs))
    return results, time.perf_counter() - started
