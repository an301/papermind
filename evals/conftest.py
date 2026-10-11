"""pytest as the eval runner: the benchmark is loaded once and every ``row`` test is parametrized over it.

    .venv/bin/pytest evals                                  # scorer unit tests + retrieval suite (no API key, ~10s)
    .venv/bin/pytest -m eval evals/test_agent.py            # the agent suite (spends money)
    .venv/bin/pytest -m eval evals/test_agent.py -k 1706.03762   # one paper's rows

A benchmark row whose gold chunk no longer exists fails
``test_benchmark_labels_resolve`` with every problem listed (a re-ingested
paper that moved its chunk ids would otherwise read as a recall regression);
the row tests then have nothing to run on. A collection error would instead
abort the whole run, unit tests included. Agent suites skip when there is no
API key; the retrieval suite skips when the embedding model or the cached
chunk vectors are not on disk, because computing them takes minutes and needs
the network.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import cache

import pytest

from evals.harness.dataset import DEFAULT_BENCHMARK, BenchmarkError, BenchmarkRow, load_benchmark

_REPORTS = pytest.StashKey[list[tuple[str, str]]]()


@cache
def _load() -> tuple[tuple[BenchmarkRow, ...], BenchmarkError | None]:
    if not DEFAULT_BENCHMARK.exists():
        return (), None
    try:
        return tuple(load_benchmark(DEFAULT_BENCHMARK)), None
    except BenchmarkError as exc:
        return (), exc


def benchmark() -> tuple[BenchmarkRow, ...]:
    """The benchmark, loaded and validated once per process; empty when missing or invalid."""
    return _load()[0]


def benchmark_error() -> BenchmarkError | None:
    return _load()[1]


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "row" in metafunc.fixturenames:
        rows = benchmark()
        metafunc.parametrize("row", rows, ids=[r.id for r in rows])


@pytest.fixture(scope="session")
def benchmark_rows() -> tuple[BenchmarkRow, ...]:
    rows = benchmark()
    if not rows:
        pytest.skip(f"no benchmark at {DEFAULT_BENCHMARK}")
    return rows


@pytest.fixture(scope="session")
def eval_report(request: pytest.FixtureRequest) -> Callable[[str, str], None]:
    """Add a titled block to the terminal summary (shown even when output is captured)."""
    reports = request.config.stash.setdefault(_REPORTS, [])

    def add(title: str, text: str) -> None:
        reports.append((title, text))

    return add


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter, config: pytest.Config) -> None:
    for title, text in config.stash.get(_REPORTS, []):
        terminalreporter.write_sep("-", title)
        terminalreporter.write_line(text)
