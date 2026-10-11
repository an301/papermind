"""(Re)generate REQUIRES edges in concepts.json with Claude. Offline; spends API money.

    .venv/bin/python scripts/build_concept_graph.py --limit 2 --dry-run   # prove the request shape
    .venv/bin/python scripts/build_concept_graph.py --only softmax attention
    .venv/bin/python scripts/build_concept_graph.py                        # every concept

For each selected concept the model is asked for its *direct* prerequisites
(structured output ``{concept, prerequisites[]}``), and each returned name is
resolved against the catalog with the same normaliser and fuzzy matcher the
runtime uses. Edges are merged into the existing ``requires`` lists:

* an edge is added only if it does not close a cycle -- checked before adding,
  against the graph including edges accepted earlier in the same run, so the
  file stays a DAG whatever order the answers arrive in;
* an edge the graph already implies transitively is skipped, which keeps
  ``requires`` to direct prerequisites (BFS depth means something only then);
* names that match no concept are reported as candidates, never invented as
  new nodes (a node needs an area, a tier and a description a human checks).

Existing edges are never removed here; delete them in the file and re-seed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Literal

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anthropic import AsyncAnthropic
from pydantic import BaseModel, Field

from papermind.config import get_settings
from papermind.graph.normalize import ConceptIndex
from papermind.graph.seed import (
    Catalog,
    ConceptSpec,
    check_files,
    default_areas_path,
    validate,
)

# Thinking is always on for this model; low effort keeps a graph-wide run
# cheap, and naming a few direct prerequisites is not a hard reasoning task.
EFFORT: Final = "low"
MAX_TOKENS = 8000


class PrerequisiteAnswer(BaseModel):
    concept: str = Field(description="The concept being analysed, exactly as named in the request.")
    prerequisites: list[str] = Field(
        description="Direct prerequisites, using catalog names wherever a catalog concept fits."
    )


SYSTEM_TEMPLATE = """\
You are building the prerequisite graph behind a reading assistant for machine-learning research papers.
When a reader asks about a concept, the assistant walks this graph to find what they are missing.

For the concept you are given, list its DIRECT prerequisites: the concepts a reader must already
understand to follow a good explanation of it.

Rules:
- Direct only. If A needs B and B needs C, then A's prerequisites include B, not C.
- Usually 1-4 prerequisites. A foundation concept may have none; return an empty list then.
- Use names exactly as they appear in the catalog below whenever a catalog concept fits.
  Name something outside the catalog only if no catalog concept covers it.
- Never list the concept itself, one of its synonyms, or something more advanced than it.

Catalog (name [id] - area, tier):
{catalog}
"""


def render_catalog(catalog: Catalog) -> str:
    return "\n".join(f"- {c.name} [{c.id}] - {c.area}, {c.tier}" for c in catalog.concepts.values())


def render_request(spec: ConceptSpec, area_name: str) -> str:
    aliases = f"\nAlso called: {', '.join(spec.aliases)}" if spec.aliases else ""
    return (
        f"Concept: {spec.name} [{spec.id}]{aliases}\n"
        f"Area: {area_name}; tier: {spec.tier}\n"
        f"Description: {spec.description}\n\n"
        "What are its direct prerequisites?"
    )


# ---------------------------------------------------------------- merging (pure)


Action = Literal["added", "exists", "self", "cycle", "implied", "unresolved"]


@dataclass(frozen=True)
class EdgeDecision:
    concept_id: str
    proposed: str
    resolved: str | None
    action: Action
    detail: str = ""


def find_path(requires: Mapping[str, Iterable[str]], start: str, goal: str) -> list[str] | None:
    """A REQUIRES path ``start -> ... -> goal`` (BFS, so the shortest), or None."""
    parent: dict[str, str | None] = {start: None}
    queue = deque([start])
    while queue:
        node = queue.popleft()
        if node == goal:
            path = [node]
            while (prev := parent[path[-1]]) is not None:
                path.append(prev)
            return path[::-1]
        for nxt in requires.get(node, ()):
            if nxt not in parent:
                parent[nxt] = node
                queue.append(nxt)
    return None


def merge_answer(
    requires: dict[str, list[str]], index: ConceptIndex, concept_id: str, names: Sequence[str]
) -> list[EdgeDecision]:
    """Fold one answer into ``requires`` (mutated in place); one decision per proposed name."""
    decisions: list[EdgeDecision] = []
    for name in names:
        resolution = index.resolve(name)
        if resolution.ref is None:
            hint = ", ".join(r.concept_id for r in resolution.suggestions[:3])
            decisions.append(
                EdgeDecision(concept_id, name, None, "unresolved", f"near: {hint}" if hint else "")
            )
            continue
        target = resolution.ref.concept_id
        current = requires.setdefault(concept_id, [])
        if target == concept_id:
            decisions.append(EdgeDecision(concept_id, name, target, "self"))
        elif target in current:
            decisions.append(EdgeDecision(concept_id, name, target, "exists"))
        elif cycle := find_path(requires, target, concept_id):
            # concept -> target would close target -> ... -> concept.
            loop = " -> ".join([concept_id, *cycle])
            decisions.append(EdgeDecision(concept_id, name, target, "cycle", loop))
        elif implied := find_path(requires, concept_id, target):
            decisions.append(EdgeDecision(concept_id, name, target, "implied", " -> ".join(implied)))
        else:
            current.append(target)
            decisions.append(EdgeDecision(concept_id, name, target, "added"))
    return decisions


# ---------------------------------------------------------------- API calls


@dataclass
class UsageTotals:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    request_ids: list[str] = field(default_factory=list)

    def add(self, usage: Any, request_id: str | None) -> None:
        self.calls += 1
        self.input_tokens += usage.input_tokens or 0
        self.output_tokens += usage.output_tokens or 0
        self.cache_creation_input_tokens += usage.cache_creation_input_tokens or 0
        self.cache_read_input_tokens += usage.cache_read_input_tokens or 0
        if request_id:
            self.request_ids.append(request_id)


@dataclass
class Answer:
    concept_id: str
    prerequisites: list[str] | None
    error: str | None = None


async def ask(
    client: AsyncAnthropic,
    *,
    model: str,
    system: str,
    spec: ConceptSpec,
    area_name: str,
    usage: UsageTotals,
) -> Answer:
    response = await client.messages.parse(
        model=model,
        max_tokens=MAX_TOKENS,
        # The catalog is identical on every call; caching it makes the per-concept
        # cost mostly the short request (once the prefix clears the cache minimum).
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": render_request(spec, area_name)}],
        output_format=PrerequisiteAnswer,
        output_config={"effort": EFFORT},
    )
    usage.add(response.usage, response._request_id)
    parsed = response.parsed_output
    if parsed is None:
        return Answer(spec.id, None, f"no structured output (stop_reason={response.stop_reason})")
    return Answer(spec.id, list(parsed.prerequisites))


# ---------------------------------------------------------------- driver


def select(catalog: Catalog, only: Sequence[str] | None, limit: int | None) -> list[ConceptSpec]:
    specs = list(catalog.concepts.values())
    if only:
        missing = [cid for cid in only if cid not in catalog.concepts]
        if missing:
            raise SystemExit(f"unknown concept ids: {', '.join(missing)}")
        wanted = set(only)
        specs = [spec for spec in specs if spec.id in wanted]
    return specs if limit is None else specs[:limit]


def write_atomic(path: Path, payload: dict[str, Any]) -> None:
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def print_decisions(answers: list[Answer], decisions: list[EdgeDecision]) -> None:
    marks = {"added": "+", "exists": "=", "self": "~", "cycle": "x", "implied": "-", "unresolved": "?"}
    by_concept: dict[str, list[EdgeDecision]] = {}
    for d in decisions:
        by_concept.setdefault(d.concept_id, []).append(d)
    for answer in answers:
        print(answer.concept_id)
        if answer.error:
            print(f"  ! {answer.error}")
        elif not answer.prerequisites:
            print("    (no prerequisites proposed)")
        for d in by_concept.get(answer.concept_id, []):
            target = d.resolved or ""
            detail = f"  ({d.detail})" if d.detail else ""
            print(f"  {marks[d.action]} {d.action:10} {d.proposed!r} -> {target}{detail}")


async def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    if not settings.has_api_key or settings.anthropic_api_key is None:
        print("ANTHROPIC_API_KEY is not set (put it in .env)", file=sys.stderr)
        return 2

    areas_path = args.areas or default_areas_path(args.concepts)
    catalog, problems = await asyncio.to_thread(check_files, args.concepts, areas_path)
    errors = [p for p in problems if p.severity == "error"]
    if catalog is None or errors:
        for p in errors:
            print(p, file=sys.stderr)
        print("fix the seed files first (scripts/validate_concepts.py)", file=sys.stderr)
        return 1

    targets = select(catalog, args.only, args.limit)
    area_names = {a.area_id: a.name for a in catalog.areas}
    system = SYSTEM_TEMPLATE.format(catalog=render_catalog(catalog))
    usage = UsageTotals()
    gate = asyncio.Semaphore(args.concurrency)

    async with AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value(), max_retries=3) as client:

        async def one(spec: ConceptSpec) -> Answer:
            async with gate:
                try:
                    return await ask(
                        client,
                        model=settings.model,
                        system=system,
                        spec=spec,
                        area_name=area_names.get(spec.area, spec.area),
                        usage=usage,
                    )
                except Exception as exc:  # one failed concept must not sink the run
                    return Answer(spec.id, None, f"{exc.__class__.__name__}: {exc}")

        # Every request shares the catalog prefix. Sent all at once, the first
        # wave would each pay to write the same cache entry; one request first
        # writes it, and the rest read it.
        answers = [await one(targets[0])] if targets else []
        answers += await asyncio.gather(*(one(spec) for spec in targets[1:]))

    # Merge in file order, not completion order, so a run is reproducible
    # given the same answers.
    requires = {cid: list(catalog.prerequisites_of(cid)) for cid in catalog.concepts}
    decisions: list[EdgeDecision] = []
    failed = [a for a in answers if a.prerequisites is None]
    for answer in answers:
        if answer.prerequisites is not None:
            decisions.extend(merge_answer(requires, catalog.index, answer.concept_id, answer.prerequisites))

    print_decisions(answers, decisions)
    added = sum(d.action == "added" for d in decisions)
    print(
        f"\n{len(targets)} concept(s), {added} edge(s) to add, "
        f"{sum(d.action == 'cycle' for d in decisions)} rejected as cycles, "
        f"{sum(d.action == 'unresolved' for d in decisions)} unresolved name(s), {len(failed)} failed call(s)"
    )
    print(
        f"usage: {usage.calls} call(s), input {usage.input_tokens}, output {usage.output_tokens}, "
        f"cache write {usage.cache_creation_input_tokens}, cache read {usage.cache_read_input_tokens}"
    )
    if usage.request_ids:
        print("request ids: " + ", ".join(usage.request_ids))

    if args.dry_run or not added:
        print("dry run: concepts.json not written" if args.dry_run else "nothing to write")
        return 0

    payload = json.loads(args.concepts.read_text(encoding="utf-8"))
    for row in payload["concepts"]:
        row["requires"] = requires.get(row["id"], row.get("requires", []))
    merged = [ConceptSpec.model_validate(row) for row in payload["concepts"]]
    remaining = [p for p in validate(catalog.areas, merged) if p.severity == "error"]
    if remaining:  # cannot happen if merge_answer is right; refuse to write a broken file regardless
        for p in remaining:
            print(p, file=sys.stderr)
        return 1
    write_atomic(args.concepts, payload)
    print(f"wrote {args.concepts}")
    return 0


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--concepts", type=Path, default=settings.concepts_path)
    parser.add_argument("--areas", type=Path, default=None, help="default: areas.json next to --concepts")
    parser.add_argument("--only", nargs="+", metavar="ID", help="concept ids to process (default: all)")
    parser.add_argument("--limit", type=int, default=None, help="process at most N concepts")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument(
        "--dry-run", action="store_true", help="call the API and report; do not write the file"
    )
    args = parser.parse_args(argv)
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
