"""Ask PaperMind one question from the terminal, with the real agent, tools and model.

    .venv/bin/python scripts/ask.py --paper 1706.03762 --level beginner "How does attention work?"

Starts the three MCP servers (stdio, or HTTP when MCP_TRANSPORT=http), runs one
turn against an in-memory session, and prints the agent's activity as it
happens, then the answer, its sources (with the sentence each citation relied
on), the other papers it cited, what it learned about the reader, suggested
follow-ups and what the turn cost. ``--no-cache`` and ``--sequential`` switch
off the two latency optimisations for a quick ablation; ``--transcript`` keeps
a session file so follow-up questions see the conversation; ``--events``
writes every event as a JSON line.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from papermind.agent.loop import TurnContext, TurnResult, build_runner
from papermind.config import Settings, get_settings
from papermind.graph.store import get_graph_store
from papermind.llm.client import LLM, PaperMindLLMError
from papermind.mcp_client.manager import MCPManager
from papermind.schemas.events import ChatEvent
from papermind.schemas.paper import PaperMeta, PaperRecord
from papermind.schemas.session import Quote, Session, UserProfile
from papermind.schemas.tools import Level


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("question")
    parser.add_argument("--paper", default="1706.03762", help="paper id under data/papers/")
    parser.add_argument("--level", choices=["beginner", "intermediate", "expert"], default="intermediate")
    parser.add_argument("--user", default="u_cli", help="reader id (their KNOWS edges live in the graph)")
    parser.add_argument("--quote", help="text 'selected' in the PDF")
    parser.add_argument("--no-cache", action="store_true", help="send no cache_control (ablation baseline)")
    parser.add_argument("--sequential", action="store_true", help="run one step's tools one at a time")
    parser.add_argument("--prewarm", action="store_true", help="write the prompt cache before the question")
    parser.add_argument("--transcript", type=Path, help="session JSON to continue and update")
    parser.add_argument("--trace", type=Path, help="write the turn's trace as JSON here")
    parser.add_argument("--events", type=Path, help="write every event as a JSON line here")
    parser.add_argument("-v", "--verbose", action="store_true", help="log every model call")
    return parser.parse_args(argv)


def load_paper(settings: Settings, paper_id: str) -> PaperMeta:
    path = settings.papers_dir / paper_id / "paper.json"
    if not path.exists():
        raise SystemExit(f"No ingested paper at {path}. Ingest it first.")
    return PaperRecord.model_validate_json(path.read_bytes()).meta


def load_session(path: Path | None, *, user_id: str, paper_id: str, level: Level) -> Session:
    if path is not None and path.exists():
        return Session.model_validate_json(path.read_text())
    now = datetime.now(UTC)
    return Session(
        session_id="s_cli", user_id=user_id, paper_id=paper_id, level=level, created_at=now, updated_at=now
    )


class Printer:
    """Turns ChatEvents into terminal lines; the answer streams in place."""

    def __init__(self) -> None:
        self.started = time.perf_counter()
        self.streaming = False
        self.events: list[ChatEvent] = []

    def _line(self, text: str) -> None:
        if self.streaming:
            print()
            self.streaming = False
        print(f"[{time.perf_counter() - self.started:6.2f}s] {text}", flush=True)

    async def __call__(self, event: ChatEvent) -> None:
        self.events.append(event)
        match event.type:
            case "turn.started":
                self.started = time.perf_counter()
                self._line(f"turn started ({event.level})")
            case "tool.started":
                self._line(f"  start  {event.label}")
            case "tool.retry":
                self._line(f"  retry  {event.tool}: {event.error}")
            case "tool.finished":
                status = "done " if event.ok else "FAILED"
                self._line(f"  {status} {event.summary} ({event.duration_ms} ms)")
            case "gaps":
                order = " -> ".join(c.name for c in event.teaching_order) or "none"
                self._line(f"  gaps   {len(event.gaps)} unknown, altitude {event.altitude}; teach: {order}")
            case "sources":
                labels = ", ".join(f"{s.label}=§{s.section_number or s.section_title}" for s in event.sources)
                self._line(f"sources {labels or 'none'}")
                self._line("answer:")
            case "answer.delta":
                print(event.text, end="", flush=True)
                self.streaming = True
            case "answer.done":
                self._line(
                    f"answer done ({len(event.text)} chars, cited {', '.join(event.cited) or 'nothing'})"
                )
                for s in event.sources:
                    where = f"§{s.section_number}" if s.section_number else s.section_title
                    used = f"“{s.cited_sentence}”" if s.cited_sentence else "(not cited)"
                    print(f"           {s.label} {where} p.{s.page_start + 1}: {used}")
                for e in event.external:
                    authors = ", ".join(e.authors[:2]) + (" et al." if len(e.authors) > 2 else "")
                    print(f"           {e.label} {e.title} ({e.year}), {authors} [{e.s2_id}]")
            case "knowledge.updated":
                added = ", ".join(c.name for c in event.added) or "-"
                removed = ", ".join(c.name for c in event.removed) or "-"
                ask = ", ".join(c.name for c in event.ask) or "-"
                self._line(f"knowledge: now known {added}; no longer known {removed}; ask about {ask}")
            case "followups":
                self._line("follow-ups:")
                for q in event.questions:
                    print(f"           - {q}")
            case "usage":
                self._line(
                    f"usage: {event.model_calls} calls, in {event.input_tokens}, "
                    f"cache read {event.cache_read_input_tokens}, cache write {event.cache_creation_input_tokens}, "
                    f"out {event.output_tokens}, ${event.cost_usd:.4f}, "
                    f"latency {event.latency_ms} ms, ttft {event.ttft_ms} ms"
                )
            case "error":
                self._line(f"ERROR {event.code}: {event.message}")
            case "done":
                self._line("done")


def print_calls(result: TurnResult) -> None:
    print("\nmodel calls:")
    print(
        f"  {'purpose':8} {'step':>4} {'in':>6} {'c.read':>7} {'c.write':>7} {'out':>6} {'ms':>6} {'ttft':>6} {'cost':>8}"
    )
    for c in result.trace.model_calls:
        print(
            f"  {c.purpose:8} {c.step if c.step is not None else '-':>4} {c.input_tokens:>6} "
            f"{c.cache_read_input_tokens:>7} {c.cache_creation_input_tokens:>7} {c.output_tokens:>6} "
            f"{c.latency_ms:>6} {c.ttft_ms if c.ttft_ms is not None else '-':>6} ${c.cost_usd:>7.4f}"
            + ("  (fallback)" if c.fell_back else "")
        )
    if result.trace.errors:
        print("trace notes:")
        for note in result.trace.errors:
            print(f"  - {note}")


async def main(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    # The servers inherit this, and their stderr shares our terminal.
    overrides: dict[str, Any] = {"log_level": "INFO" if args.verbose else "WARNING"}
    if args.no_cache:
        overrides["prompt_caching"] = False
    if args.sequential:
        overrides["parallel_tools"] = False
    settings = get_settings().model_copy(update=overrides)
    level: Level = args.level

    paper = await asyncio.to_thread(load_paper, settings, args.paper)
    session = load_session(args.transcript, user_id=args.user, paper_id=paper.paper_id, level=level)
    try:
        llm = LLM(settings)
    except PaperMindLLMError as exc:
        print(exc.message, file=sys.stderr)
        return 2

    graph = await get_graph_store(settings)
    mcp = MCPManager(settings)
    try:
        started = time.perf_counter()
        await mcp.start()
        print(f"MCP servers up in {time.perf_counter() - started:.1f}s ({settings.mcp_transport})")
        runner = build_runner(settings, llm=llm, mcp=mcp, knowledge_source=graph.known)
        if args.prewarm:
            warmed = await runner.prewarm(paper)
            written = sum(r.usage.cache_creation_input_tokens for r in warmed)
            print(f"prewarmed {len(warmed)} prefixes ({written} tokens written)")

        knowledge = await graph.known(args.user)
        known = sorted(
            (k.name for k in knowledge.values() if k.confidence >= settings.known_threshold), key=str.casefold
        )
        ctx = TurnContext(
            session=session,
            user=UserProfile(user_id=args.user, level=level, created_at=datetime.now(UTC)),
            paper=paper,
            question=args.question,
            quote=Quote(text=args.quote) if args.quote else None,
            level=level,
            known_summary=known,
            knowledge=knowledge,
        )
        print(
            f"{paper.title} | {level} | knows {len(known)} concepts | cache {'on' if settings.prompt_caching else 'off'} "
            f"| tools {'parallel' if settings.parallel_tools else 'sequential'}"
        )
        printer = Printer()
        result = await runner.run_turn(ctx, printer)
        print_calls(result)

        if args.transcript is not None:
            updated = session.model_copy(
                update={
                    "messages": [*session.messages, result.user_message, result.assistant_message],
                    "updated_at": datetime.now(UTC),
                }
            )
            args.transcript.write_text(updated.model_dump_json(indent=2))
        if args.trace is not None:
            args.trace.write_text(json.dumps(result.trace.to_dict(), indent=2, default=str))
        if args.events is not None:
            printer_events = "".join(e.model_dump_json() + "\n" for e in printer.events)
            args.events.write_text(printer_events)
        return 1 if result.error else 0
    finally:
        await mcp.aclose()
        await graph.aclose()
        await llm.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(parse_args())))
