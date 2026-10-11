"""Ingest papers from the command line.

    python -m papermind.ingest --arxiv 1706.03762v7 --arxiv https://arxiv.org/abs/1810.04805
    python -m papermind.ingest --file paper.pdf
    python -m papermind.ingest --from-file evals/benchmark/papers.txt

``--from-file`` reads one arXiv id or URL per line; text after '#' is a
comment. Progress goes to stderr, one summary line per paper to stdout.
Papers are ingested one after another: arXiv's courtesy limit is global, and
embedding already uses every core.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from papermind.config import get_settings
from papermind.ingest.arxiv import ArxivError
from papermind.ingest.extract import PDFError
from papermind.ingest.pipeline import Ingestor, default_ingestor
from papermind.schemas.paper import IngestStage, PaperRecord


def read_id_list(path: Path) -> list[str]:
    ids: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        entry = raw.split("#", 1)[0].strip()
        if entry:
            ids.append(entry.split()[0])
    return ids


def _progress(label: str) -> Callable[[IngestStage, float], Awaitable[None]]:
    last: dict[str, object] = {"stage": None, "tenth": -1}

    async def report(stage: IngestStage, fraction: float) -> None:
        tenth = int(fraction * 10)
        if stage != last["stage"] or tenth != last["tenth"]:
            last["stage"], last["tenth"] = stage, tenth
            print(f"  {label}: {stage.value:<11} {fraction:4.0%}", file=sys.stderr)

    return report


def summary(record: PaperRecord, seconds: float) -> str:
    meta = record.meta
    version = meta.arxiv_version or ""
    return (
        f"{meta.paper_id}{version}\t{meta.num_pages} pages\t{len(meta.sections)} sections\t"
        f"{len(record.chunks)} chunks\t{seconds:.1f}s\t{meta.title}"
    )


async def run(arxiv: list[str], files: list[Path], ingestor: Ingestor | None = None) -> int:
    ingestor = ingestor or default_ingestor()
    failures = 0
    jobs: list[tuple[str, Callable[[], Awaitable[PaperRecord]]]] = []
    for raw in arxiv:
        jobs.append((raw, lambda raw=raw: ingestor.ingest_arxiv(raw, on_progress=_progress(raw))))
    for path in files:
        jobs.append(
            (
                str(path),
                lambda path=path: ingestor.ingest_upload(
                    path.read_bytes(), path.name, on_progress=_progress(path.name)
                ),
            )
        )
    for label, job in jobs:
        started = time.perf_counter()
        try:
            record = await job()
        except (ArxivError, PDFError, OSError) as exc:
            failures += 1
            print(f"FAILED {label}: {exc}", file=sys.stderr)
            continue
        print(summary(record, time.perf_counter() - started), flush=True)
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m papermind.ingest",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--arxiv", action="append", default=[], metavar="ID", help="arXiv id or URL")
    parser.add_argument("--file", action="append", default=[], type=Path, metavar="PDF", help="a local PDF")
    parser.add_argument("--from-file", type=Path, metavar="LIST", help="file of arXiv ids, one per line")
    args = parser.parse_args(argv)
    arxiv = list(args.arxiv)
    if args.from_file is not None:
        arxiv.extend(read_id_list(args.from_file))
    if not arxiv and not args.file:
        parser.error("nothing to ingest: pass --arxiv, --file or --from-file")
    logging.basicConfig(level=get_settings().log_level, format="%(levelname)s %(name)s: %(message)s")
    return asyncio.run(run(arxiv, list(args.file)))


if __name__ == "__main__":
    sys.exit(main())
