"""Ingest the eight benchmark papers at their pinned arXiv versions.

    python scripts/ingest_benchmark.py            # ingest what is missing or stale
    python scripts/ingest_benchmark.py --force    # re-ingest everything
    python scripts/ingest_benchmark.py --smoke    # then run the retrieval smoke test

A paper is skipped when data/papers/{id}/paper.json already holds the pinned
version produced by the current CHUNKER_VERSION; anything else is re-ingested,
because chunk ids (the eval labels) depend on both.

``--smoke`` asks three natural questions per paper through all three retrieval
paths (BM25, dense, hybrid) and prints the top-5 section numbers, whether the
section that answers the question is among them, and per-query latency after
warm-up. It needs no API key; it does need the embedding model.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from papermind.config import get_settings  # noqa: E402
from papermind.ingest.__main__ import read_id_list, summary  # noqa: E402
from papermind.ingest.arxiv import parse_arxiv_id  # noqa: E402
from papermind.ingest.ids import CHUNKER_VERSION  # noqa: E402
from papermind.ingest.pipeline import arxiv_paper_id, default_ingestor  # noqa: E402
from papermind.retrieval.dense import EmbeddingCache, get_embedder  # noqa: E402
from papermind.retrieval.hybrid import HybridRetriever  # noqa: E402
from papermind.retrieval.index import IndexRegistry  # noqa: E402

PAPERS_FILE = REPO / "evals" / "benchmark" / "papers.txt"

# (question, section numbers that answer it). Phrased the way a reader asks,
# not in the paper's vocabulary: the point is to see where each arm breaks.
SMOKE_QUESTIONS: dict[str, list[tuple[str, set[str]]]] = {
    "1706.03762": [
        ("why divide by the square root of d_k", {"3.2.1"}),
        ("how much compute did training take", {"5.2"}),
        ("how does the model know the order of the words without recurrence", {"3.5"}),
    ],
    "1810.04805": [
        ("how are tokens masked during pre-training", {"3.1", "A.1", "C.2"}),
        ("what is the next sentence prediction task", {"3.1", "A.1"}),
        ("how does it handle questions that have no answer", {"4.3"}),
    ],
    "1512.03385": [
        ("why do deeper plain networks get worse training error", {"1", "4.1"}),
        ("what does the shortcut do when the dimensions change", {"3.2", "3.3"}),
        ("bottleneck blocks for the 50 101 and 152 layer nets", {"4.1"}),
    ],
    "1412.6980": [
        ("why do the moment estimates need bias correction", {"3"}),
        ("what are the default values of beta1 beta2 and epsilon", {"1", "2"}),
        ("what is the infinity norm variant", {"7.1"}),
    ],
    "2106.09685": [
        ("why is there no extra inference latency", {"4.1", "3", "B"}),
        ("which weight matrices should the low rank update be applied to", {"7.1"}),
        ("how are A and B initialized", {"4.1"}),
    ],
    "2305.18290": [
        ("how does it avoid training a separate reward model", {"4", "5.1"}),
        ("what is the Bradley-Terry model of preferences", {"3", "A.2"}),
        ("is GPT-4 a trustworthy judge compared to humans", {"6.4", "D.3"}),
    ],
    "2010.11929": [
        ("how is an image turned into a sequence of tokens", {"3.1"}),
        ("how much pre-training data is needed to beat ResNets", {"4.3"}),
        ("what do the learned position embeddings look like", {"4.5", "D.4"}),
    ],
    "1502.03167": [
        ("what is internal covariate shift", {"1", "2"}),
        ("what happens at inference time with the mini-batch statistics", {"3.1"}),
        ("why can it use higher learning rates", {"3.3"}),
    ],
}


def pinned(path: Path) -> list[str]:
    return read_id_list(path)


def is_current(versioned: str) -> bool:
    ref = parse_arxiv_id(versioned)
    meta = default_ingestor().store.meta(arxiv_paper_id(ref.arxiv_id))
    return meta is not None and meta.arxiv_version == ref.version and meta.chunker_version == CHUNKER_VERSION


async def ingest(ids: list[str], *, force: bool) -> int:
    ingestor = default_ingestor()
    failures = 0
    for versioned in ids:
        if not force and is_current(versioned):
            print(f"{versioned}\tup to date (chunker v{CHUNKER_VERSION})", flush=True)
            continue
        started = time.perf_counter()
        try:
            record = await ingestor.ingest_arxiv(versioned)
        except Exception as exc:
            failures += 1
            print(f"FAILED {versioned}: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        print(summary(record, time.perf_counter() - started), flush=True)
    return failures


def _section(chunk_id: str, numbers: dict[str, str | None]) -> str:
    return numbers.get(chunk_id) or chunk_id.split(":", 1)[1]


async def smoke(ids: list[str]) -> None:
    settings = get_settings()
    registry = IndexRegistry(
        default_ingestor().store, get_embedder(), cache=EmbeddingCache(settings.cache_dir / "embeddings")
    )
    retriever = HybridRetriever(registry)
    papers = [arxiv_paper_id(parse_arxiv_id(v).arxiv_id) for v in ids]
    await asyncio.to_thread(registry.embedder.embed_query, "warm-up")
    for paper_id in papers:
        await registry.get(paper_id)

    modes = list(retriever.paths())
    first_hit: dict[str, list[int | None]] = {mode: [] for mode in modes}
    latency: dict[str, list[float]] = {mode: [] for mode in modes}
    for paper_id in papers:
        index = await registry.get(paper_id)
        numbers = {c.chunk_id: c.section_number for c in index.chunks}
        print(f"\n## {paper_id}")
        for question, expected in SMOKE_QUESTIONS.get(paper_id, []):
            print(f"  Q: {question}   (expect {', '.join(sorted(expected))})")
            for mode, path in retriever.paths().items():
                started = time.perf_counter()
                results = await path(question, paper_id, 5)
                latency[mode].append((time.perf_counter() - started) * 1000)
                sections = [_section(r.chunk_id, numbers) for r in results]
                rank = next((r for r, s in enumerate(sections, start=1) if s in expected), None)
                first_hit[mode].append(rank)
                print(f"    {mode:<6} {f'@{rank}' if rank else 'miss':<4} {sections}")
    total = len(first_hit[modes[0]])
    print(f"\n{total} questions, expected section among the top 5:")
    for mode in modes:
        ranks = first_hit[mode]
        at1 = sum(rank == 1 for rank in ranks)
        at5 = sum(rank is not None for rank in ranks)
        mrr = sum(1 / rank for rank in ranks if rank) / max(total, 1)
        print(f"  {mode:<6} hit@1={at1:>2}/{total}  hit@5={at5:>2}/{total}  MRR@5={mrr:.3f}")
    print("latency after warm-up (model loaded, indexes built; includes query embedding):")
    for mode, values in latency.items():
        values.sort()
        p95 = values[min(len(values) - 1, int(0.95 * len(values)))]
        print(f"  {mode:<6} p50={statistics.median(values):6.1f} ms  p95={p95:6.1f} ms")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--papers", type=Path, default=PAPERS_FILE, help="pinned id list (default: %(default)s)")
    parser.add_argument("--force", action="store_true", help="re-ingest papers that are already current")
    parser.add_argument("--smoke", action="store_true", help="run the retrieval smoke test afterwards")
    parser.add_argument("--skip-ingest", action="store_true", help="only run the smoke test")
    args = parser.parse_args(argv)
    ids = pinned(args.papers)
    failures = 0 if args.skip_ingest else asyncio.run(ingest(ids, force=args.force))
    if args.smoke:
        asyncio.run(smoke(ids))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
