"""The writer suite's plumbing with a fake writer and no judges: no API, no GPU."""

from __future__ import annotations

from collections.abc import AsyncIterator

from evals.harness.dataset import load_benchmark, select_rows
from evals.harness.writer_suite import run_writer, summarize_writer
from papermind.agent.writer import WriterInput


class EchoWriter:
    name = "fake:echo"

    def __init__(self) -> None:
        self.inputs: list[WriterInput] = []

    async def stream(self, inp: WriterInput) -> AsyncIterator[str]:
        self.inputs.append(inp)
        yield f"At {inp.level}: "
        yield "an answer [S1]."


async def test_every_writer_gets_the_gold_passage_and_the_requested_level() -> None:
    rows = select_rows(load_benchmark(), 3)
    writer = EchoWriter()
    results = await run_writer(rows, writer, judges=None, concurrency=2)
    assert [r.answer for r in results] == [f"At {row.target_level}: an answer [S1]." for row in rows]
    for inp, row in zip(sorted(writer.inputs, key=lambda i: i.question), sorted(rows, key=lambda r: r.question), strict=True):
        assert inp.level == row.target_level
        assert [s.ref.chunk_id for s in inp.sources] == [row.gold_chunk]  # retrieval held fixed
    summary = summarize_writer(results)
    assert summary["n"] == 3 and summary["errors"] == 0 and summary["calibration"] is None  # no judge, no label
