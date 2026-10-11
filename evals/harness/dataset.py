"""The benchmark as typed rows, validated against the ingested papers.

A row's ``gold_chunk`` is a chunk id, and chunk ids are eval labels: they are
derived from section numbers and titles at ingestion time. A re-ingested paper
(new arXiv version, a chunker change) can silently move a label, and recall
against a label that no longer exists is just zero -- indistinguishable from a
retrieval regression. So loading checks every label against
``data/papers/<paper_id>/paper.json`` and reports every problem at once instead
of failing on the first.

    .venv/bin/python -m evals.harness.dataset [path]      # validate + counts
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from collections.abc import Iterable, Sequence
from functools import cached_property
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from papermind.config import REPO_ROOT, get_settings
from papermind.ingest.store import PaperStore
from papermind.schemas.paper import Chunk, PaperRecord
from papermind.schemas.tools import Level

BENCHMARK_DIR = REPO_ROOT / "evals" / "benchmark"
DEFAULT_BENCHMARK = BENCHMARK_DIR / "questions.jsonl"
DEFAULT_HAND_LABELS = BENCHMARK_DIR / "hand_labels.csv"
RESULTS_DIR = REPO_ROOT / "evals" / "results"

LEVELS: tuple[Level, ...] = ("beginner", "intermediate", "expert")
Origin = Literal["generated", "natural"]
ORIGINS: tuple[Origin, ...] = ("generated", "natural")
QueryType = Literal["lookup", "conceptual", "numeric", "method", "comparison", "motivation"]
QUERY_TYPES: tuple[QueryType, ...] = ("lookup", "conceptual", "numeric", "method", "comparison", "motivation")


class BenchmarkRow(BaseModel):
    """One benchmark question. The schema is the contract between the data and the harness."""

    # A misspelled field ("gold_chunks") must fail loudly, not load as a row
    # with no label.
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    paper_id: str = Field(min_length=1)
    question: str = Field(min_length=3)
    target_level: Level
    gold_chunk: str = Field(min_length=1)
    reference_answer: str = Field(min_length=1)
    origin: Origin
    query_type: QueryType


class BenchmarkError(ValueError):
    """The benchmark file has problems; ``problems`` lists every one of them."""

    def __init__(self, path: Path, problems: list[str]) -> None:
        shown = "\n  ".join(problems[:50])
        more = f"\n  ... and {len(problems) - 50} more" if len(problems) > 50 else ""
        super().__init__(f"{path}: {len(problems)} problem(s)\n  {shown}{more}")
        self.path = path
        self.problems = problems


class PaperLibrary:
    """Ingested papers by id, each loaded at most once. Shared by validation, the runner and the judges."""

    def __init__(self, papers_dir: Path | None = None) -> None:
        self.store = PaperStore(papers_dir or get_settings().papers_dir)
        self._records: dict[str, PaperRecord | None] = {}

    def record(self, paper_id: str) -> PaperRecord | None:
        if paper_id not in self._records:
            self._records[paper_id] = self.store.load(paper_id)
        return self._records[paper_id]

    def chunks(self, paper_id: str) -> dict[str, Chunk]:
        record = self.record(paper_id)
        return {} if record is None else {c.chunk_id: c for c in record.chunks}

    def chunk_text(self, paper_id: str, chunk_id: str) -> str | None:
        chunk = self.chunks(paper_id).get(chunk_id)
        return None if chunk is None else chunk.text


def _parse_lines(path: Path) -> tuple[list[BenchmarkRow], list[str]]:
    rows: list[BenchmarkRow] = []
    problems: list[str] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rows.append(BenchmarkRow.model_validate_json(line))
        except ValidationError as exc:
            fields = "; ".join(
                f"{'.'.join(str(p) for p in e['loc']) or 'row'}: {e['msg']}"
                for e in exc.errors(include_url=False)
            )
            problems.append(f"line {lineno}: {fields}")
    return rows, problems


def _row_problems(rows: Sequence[BenchmarkRow], library: PaperLibrary) -> list[tuple[str, str]]:
    """(row id, problem) pairs: duplicate ids, unknown papers, labels that do not exist."""
    problems: list[tuple[str, str]] = []
    for row_id, count in Counter(r.id for r in rows).items():
        if count > 1:
            problems.append((row_id, f"id appears {count} times"))
    for row in rows:
        if library.record(row.paper_id) is None:
            problems.append((row.id, f"paper {row.paper_id} is not ingested (no paper.json)"))
        elif row.gold_chunk not in library.chunks(row.paper_id):
            problems.append(
                (row.id, f"gold_chunk {row.gold_chunk!r} does not exist in {row.paper_id}/paper.json")
            )
        elif not row.gold_chunk.startswith(f"{row.paper_id}:"):
            problems.append((row.id, f"gold_chunk {row.gold_chunk!r} belongs to another paper"))
    return problems


def validate_rows(rows: Sequence[BenchmarkRow], library: PaperLibrary) -> list[str]:
    """Every problem with these rows, one readable line each."""
    return [f"{row_id}: {problem}" for row_id, problem in _row_problems(rows, library)]


def load_benchmark(
    path: Path | str = DEFAULT_BENCHMARK,
    origin: Origin | None = None,
    papers: Iterable[str] | None = None,
    *,
    library: PaperLibrary | None = None,
    strict: bool = True,
) -> list[BenchmarkRow]:
    """Rows of the benchmark, filtered by ``origin`` and ``papers``, in file order.

    Filters apply before label validation, so a run restricted to two papers
    does not fail on a problem in a third. ``strict=False`` drops bad rows
    instead of raising (the problems are still on ``BenchmarkError`` when
    strict).
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"no benchmark at {path}")
    rows, problems = _parse_lines(path)
    wanted = set(papers) if papers else None
    rows = [
        r for r in rows if (origin is None or r.origin == origin) and (wanted is None or r.paper_id in wanted)
    ]
    library = library or PaperLibrary()
    row_problems = _row_problems(rows, library)
    problems += [f"{row_id}: {problem}" for row_id, problem in row_problems]
    if problems and strict:
        raise BenchmarkError(path, problems)
    bad_ids = {row_id for row_id, _ in row_problems}
    return [r for r in rows if r.id not in bad_ids]


def answer_text_hash(text: str) -> str:
    """Identity of one generated answer, so a hand label stays tied to the text it judged.

    Re-running the agent produces a different answer for the same row id; a
    human label must not silently transfer to it.
    """
    return hashlib.sha256(" ".join(text.split()).encode("utf-8")).hexdigest()[:12]


def file_digest(path: Path) -> str:
    """Short content hash of the benchmark file, stored with every result so runs are comparable."""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def counts(rows: Sequence[BenchmarkRow]) -> dict[str, dict[str, int]]:
    return {
        "paper": dict(sorted(Counter(r.paper_id for r in rows).items())),
        "level": {lvl: n for lvl in LEVELS if (n := sum(r.target_level == lvl for r in rows))},
        "origin": {o: n for o in ORIGINS if (n := sum(r.origin == o for r in rows))},
        "query_type": {q: n for q in QUERY_TYPES if (n := sum(r.query_type == q for r in rows))},
    }


def select_rows(rows: Sequence[BenchmarkRow], limit: int | None) -> list[BenchmarkRow]:
    """A deterministic, balanced subset of ``limit`` rows (all rows when ``limit`` is None).

    Round-robin over (paper, level) groups in file order, so ``--limit 24``
    covers every paper and level instead of the first paper's questions, and
    the same limit always picks the same questions -- the latency A/B depends
    on both configs answering exactly the same set.
    """
    if limit is None or limit >= len(rows):
        return list(rows)
    groups: dict[tuple[str, str], list[BenchmarkRow]] = {}
    for row in rows:
        groups.setdefault((row.paper_id, row.target_level), []).append(row)
    queues = list(groups.values())
    picked: list[BenchmarkRow] = []
    depth = 0
    while len(picked) < limit:
        added = False
        for queue in queues:
            if depth < len(queue) and len(picked) < limit:
                picked.append(queue[depth])
                added = True
        if not added:
            break
        depth += 1
    order = {row.id: i for i, row in enumerate(rows)}
    return sorted(picked, key=lambda r: order[r.id])


class Benchmark:
    """A loaded benchmark plus where it came from, for result files."""

    def __init__(self, path: Path, rows: list[BenchmarkRow]) -> None:
        self.path = path
        self.rows = rows

    @cached_property
    def digest(self) -> str:
        return file_digest(self.path)

    def describe(self) -> dict[str, object]:
        try:
            shown = str(self.path.relative_to(REPO_ROOT))
        except ValueError:
            shown = str(self.path)
        return {"path": shown, "sha256_16": self.digest, "rows": len(self.rows), "counts": counts(self.rows)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the benchmark and print its counts.")
    parser.add_argument("path", nargs="?", type=Path, default=DEFAULT_BENCHMARK)
    args = parser.parse_args(argv)
    try:
        rows = load_benchmark(args.path)
    except BenchmarkError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(json.dumps({"rows": len(rows), **counts(rows)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
