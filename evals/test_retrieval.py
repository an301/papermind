"""Retrieval suite under pytest: no API key, runs with the default ``pytest`` (~10s with cached vectors).

The whole benchmark goes through every mode once, in a session fixture; the
per-row tests read that run, so ``-k`` selection or test order cannot change
the aggregate the floor is checked against.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from evals.conftest import benchmark_error
from evals.harness.dataset import DEFAULT_BENCHMARK, BenchmarkRow
from evals.harness.report import retrieval_tables
from evals.harness.retrieval import (
    DEFAULT_KS,
    DEFAULT_MODES,
    RetrievalRowResult,
    missing_embeddings,
    model_downloaded,
    summarize_retrieval,
    timed_run,
)

# Regression floor: the measured hybrid recall@5 on the full benchmark minus
# about five points. Raise it when retrieval genuinely improves; lowering it
# needs a reason in the commit message.
HYBRID_RECALL_AT_5_FLOOR = 0.84

RetrievalRun = tuple[dict[str, RetrievalRowResult], dict]


@pytest.fixture(scope="session")
def retrieval_run(
    benchmark_rows: tuple[BenchmarkRow, ...], eval_report: Callable[[str, str], None]
) -> RetrievalRun:
    if not model_downloaded():
        pytest.skip("embedding model not downloaded; run `make ingest-benchmark` once (needs the network)")
    if missing := missing_embeddings([r.paper_id for r in benchmark_rows]):
        pytest.skip(f"no cached chunk vectors for {missing}; run `make ingest-benchmark` (minutes on CPU)")
    results, seconds = timed_run(benchmark_rows, modes=DEFAULT_MODES, ks=DEFAULT_KS)
    summary = summarize_retrieval(results, DEFAULT_MODES, DEFAULT_KS)
    eval_report(
        "retrieval ablation", f"{retrieval_tables(summary)}\n({len(results)} questions in {seconds:.1f}s)"
    )
    return {r.id: r for r in results}, summary


def test_benchmark_labels_resolve() -> None:
    """Every row parses and every gold chunk exists in its paper.json (runs without the embedding model)."""
    if (error := benchmark_error()) is not None:
        pytest.fail(str(error), pytrace=False)
    if not DEFAULT_BENCHMARK.exists():
        pytest.skip(f"no benchmark at {DEFAULT_BENCHMARK}")


def test_hybrid_row(row: BenchmarkRow, retrieval_run: RetrievalRun, record_property: Callable) -> None:
    results, _ = retrieval_run
    hybrid = results[row.id].modes["hybrid"]
    for name, hit in hybrid.hits.items():
        record_property(f"hybrid {name}", hit)
    record_property("hybrid rank", hybrid.rank_deep)
    # Structural checks only: a single question missing its chunk is data,
    # not a failure; the floor below is the regression test.
    assert len(hybrid.top) == len(set(hybrid.top)), "duplicate chunk ids in one result"
    assert len(hybrid.top) <= max(DEFAULT_KS)
    assert all(cid.startswith(f"{row.paper_id}:") for cid in hybrid.top), "result from another paper"


def test_hybrid_recall_at_5_floor(retrieval_run: RetrievalRun) -> None:
    _, summary = retrieval_run
    recall = summary["overall"]["hybrid"]["recall@5"]
    assert recall >= HYBRID_RECALL_AT_5_FLOOR, (
        f"hybrid recall@5 {recall:.3f} fell below the floor {HYBRID_RECALL_AT_5_FLOOR}\n{retrieval_tables(summary)}"
    )


def test_every_mode_ran(retrieval_run: RetrievalRun) -> None:
    results, summary = retrieval_run
    assert summary["n"] == len(results) > 0
    for mode in DEFAULT_MODES:
        assert all(mode in r.modes for r in results.values())
        for k in DEFAULT_KS:
            assert summary["overall"][mode][f"recall@{k}"] is not None
