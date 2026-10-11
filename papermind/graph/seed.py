"""Concept seed data: file models, validation, and the idempotent Neo4j load.

``data/concepts/concepts.json`` and ``areas.json`` are the source of truth for
the concept graph. They are generated offline by a model
(``scripts/build_concept_graph.py``), so this module assumes nothing about
their hygiene: every problem is collected and reported at once, with a cycle
reported as its full path, because a model-written graph tends to fail in
several places at once and fixing them one run at a time is miserable.

Neo4j layout written by ``seed_neo4j``::

    (:Area {id, name, description})
    (:Concept {id, name, aliases, area, tier, description, retired})
    (:Concept)-[:REQUIRES]->(:Concept)

Areas are nodes (they carry a name and a description the onboarding UI shows)
and each concept also stores its ``area`` id as a property. There is no
``IN_AREA`` relationship: every area query is "concepts with area = x", which a
property answers without a join.
"""

from __future__ import annotations

import json
import re
from collections import Counter, deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from papermind.graph.normalize import ConceptIndex, IndexEntry, compact_key, normalize_concept
from papermind.schemas.session import ConceptInfo

if TYPE_CHECKING:
    from neo4j import AsyncDriver, AsyncManagedTransaction, AsyncTransaction

Tier = Literal["foundation", "core", "advanced"]
_Row = TypeVar("_Row", bound=BaseModel)
_TIER_RANK: dict[str, int] = {"foundation": 0, "core": 1, "advanced": 2}

# Ids appear in URLs (/api/concepts/{id}) and in eval labels, so they are
# held to a strict slug shape rather than whatever the generator emitted.
ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


# ---------------------------------------------------------------- file models


class _Strict(BaseModel):
    # A misspelt key ("prerequisites" for "requires") must be an error: with
    # extra="ignore" it would silently drop every edge of that concept.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AreaSpec(_Strict):
    area_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)


class AreasFile(_Strict):
    areas: list[AreaSpec]


class ConceptSpec(_Strict):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    area: str = Field(min_length=1)
    tier: Tier
    description: str = Field(min_length=1)
    requires: list[str] = Field(default_factory=list)


class ConceptsFile(_Strict):
    version: int = 1
    concepts: list[ConceptSpec]


# ---------------------------------------------------------------- problems


Severity = Literal["error", "warning"]


@dataclass(frozen=True)
class Problem:
    severity: Severity
    code: str
    message: str
    concept_id: str | None = None

    def __str__(self) -> str:
        return f"{self.severity.upper():7} [{self.code}] {self.message}"


class CatalogError(ValueError):
    def __init__(self, problems: Sequence[Problem]) -> None:
        self.problems = list(problems)
        errors = [p for p in self.problems if p.severity == "error"]
        head = "\n".join(str(p) for p in errors[:20])
        more = f"\n... and {len(errors) - 20} more" if len(errors) > 20 else ""
        super().__init__(f"{len(errors)} error(s) in the concept catalog:\n{head}{more}")


def _error(code: str, message: str, concept_id: str | None = None) -> Problem:
    return Problem("error", code, message, concept_id)


def _warning(code: str, message: str, concept_id: str | None = None) -> Problem:
    return Problem("warning", code, message, concept_id)


# ---------------------------------------------------------------- parsing


def _read_json(path: Path, problems: list[Problem]) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        problems.append(_error("missing-file", f"{path} does not exist"))
    except json.JSONDecodeError as exc:
        problems.append(_error("bad-json", f"{path}: {exc}"))
    return None


def _format_validation(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in err['loc']) or '(row)'}: {err['msg']}" for err in exc.errors()
    )


def _parse_rows(payload: Any, key: str, model: type[_Row], label: str, problems: list[Problem]) -> list[_Row]:
    """Validate row by row so one bad row is reported without hiding the others."""
    if not isinstance(payload, dict) or not isinstance(payload.get(key), list):
        problems.append(_error("schema", f"{label}: expected an object with a '{key}' list"))
        return []
    extra = sorted(set(payload) - {key, "version"})
    if extra:
        problems.append(_error("schema", f"{label}: unexpected top-level keys {extra}"))
    rows: list[_Row] = []
    for i, raw in enumerate(payload[key]):
        try:
            rows.append(model.model_validate(raw))
        except ValidationError as exc:
            ident = (raw.get("id") or raw.get("area_id")) if isinstance(raw, dict) else None
            where = f"{label}[{i}]" + (f" ({ident})" if ident else "")
            problems.append(_error("schema", f"{where}: {_format_validation(exc)}", ident))
    return rows


def parse_areas(payload: Any, problems: list[Problem]) -> list[AreaSpec]:
    return _parse_rows(payload, "areas", AreaSpec, "areas", problems)


def parse_concepts(payload: Any, problems: list[Problem]) -> list[ConceptSpec]:
    if isinstance(payload, dict) and payload.get("version", 1) != 1:
        problems.append(_error("schema", f"concepts: unsupported version {payload.get('version')!r}"))
    return _parse_rows(payload, "concepts", ConceptSpec, "concepts", problems)


# ---------------------------------------------------------------- graph checks


def strongly_connected_components(nodes: Iterable[str], edges: dict[str, list[str]]) -> list[list[str]]:
    """Tarjan's algorithm, iterative (a recursive DFS can hit the recursion limit on long chains)."""
    index_of: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    components: list[list[str]] = []
    counter = 0
    for root in nodes:
        if root in index_of:
            continue
        work: list[tuple[str, int]] = [(root, 0)]
        while work:
            node, child_pos = work[-1]
            if child_pos == 0:
                index_of[node] = lowlink[node] = counter
                counter += 1
                stack.append(node)
                on_stack.add(node)
            children = edges.get(node, [])
            if child_pos < len(children):
                work[-1] = (node, child_pos + 1)
                child = children[child_pos]
                if child not in index_of:
                    work.append((child, 0))
                elif child in on_stack:
                    lowlink[node] = min(lowlink[node], index_of[child])
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                lowlink[parent] = min(lowlink[parent], lowlink[node])
            if lowlink[node] == index_of[node]:
                component: list[str] = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                components.append(component)
    return components


def _cycle_through(start: str, members: set[str], edges: dict[str, list[str]]) -> list[str]:
    """Shortest cycle from ``start`` back to itself inside one strongly connected component."""
    parent: dict[str, str] = {}
    queue = deque([start])
    while queue:
        node = queue.popleft()
        for child in edges.get(node, []):
            if child not in members:
                continue
            if child == start:
                path = [node]
                while path[-1] != start:
                    path.append(parent[path[-1]])
                return [*reversed(path), start]
            if child not in parent:
                parent[child] = node
                queue.append(child)
    return [start, start]  # unreachable for a genuine component; keeps the type total


def find_cycles(edges: dict[str, list[str]]) -> list[list[str]]:
    """One concrete cycle per cyclic region of the graph, e.g. ``[a, b, c, a]``.

    Enumerating every elementary cycle can be exponential; one witness per
    strongly connected component is enough to locate and fix each problem.
    """
    nodes = sorted(set(edges) | {c for children in edges.values() for c in children})
    cycles = []
    for component in strongly_connected_components(nodes, edges):
        if len(component) > 1:
            members = set(component)
            cycles.append(_cycle_through(min(members), members, edges))
    return sorted(cycles)


def validate(areas: Sequence[AreaSpec], concepts: Sequence[ConceptSpec]) -> list[Problem]:
    problems: list[Problem] = []

    area_ids = Counter(a.area_id for a in areas)
    for area_id, n in sorted(area_ids.items()):
        if n > 1:
            problems.append(_error("duplicate-area", f"area '{area_id}' is defined {n} times"))

    ids = Counter(c.id for c in concepts)
    for cid, n in sorted(ids.items()):
        if n > 1:
            problems.append(_error("duplicate-id", f"concept id '{cid}' is defined {n} times", cid))

    by_id: dict[str, ConceptSpec] = {}
    for c in concepts:
        by_id.setdefault(c.id, c)

    owner_of_key: dict[str, tuple[str, str]] = {}
    owner_of_compact: dict[str, tuple[str, str]] = {}
    for c in concepts:
        if not ID_PATTERN.match(c.id):
            problems.append(_error("bad-id", f"'{c.id}' is not a lowercase kebab-case slug", c.id))
        if c.area not in area_ids:
            problems.append(_error("unknown-area", f"{c.id}: area '{c.area}' is not in areas.json", c.id))
        seen_here: set[str] = set()
        for surface in (c.id, c.name, *c.aliases):
            key = normalize_concept(surface)
            if not key:
                problems.append(_error("empty-key", f"{c.id}: '{surface}' normalises to nothing", c.id))
                continue
            if key in seen_here:
                continue
            seen_here.add(key)
            owner = owner_of_key.setdefault(key, (c.id, surface))
            if owner[0] != c.id:
                problems.append(
                    _error(
                        "name-collision",
                        f"'{surface}' ({c.id}) and '{owner[1]}' ({owner[0]}) both normalise to '{key}'",
                        c.id,
                    )
                )
                continue
            # Space-insensitive lookups ("batch norm" -> "batchnorm") go to
            # whichever concept claimed the compact key first.
            compact_owner = owner_of_compact.setdefault(compact_key(key), (c.id, surface))
            if compact_owner[0] != c.id:
                problems.append(
                    _warning(
                        "compact-collision",
                        f"'{surface}' ({c.id}) and '{compact_owner[1]}' ({compact_owner[0]}) differ only in spacing",
                        c.id,
                    )
                )

        requires = Counter(c.requires)
        for dup, n in sorted(requires.items()):
            if n > 1:
                problems.append(_warning("duplicate-requires", f"{c.id} lists '{dup}' {n} times", c.id))
        for req in requires:
            if req == c.id:
                problems.append(_error("self-loop", f"{c.id} requires itself", c.id))
            elif req not in by_id:
                problems.append(_error("dangling-requires", f"{c.id} requires unknown concept '{req}'", c.id))
            elif _TIER_RANK[by_id[req].tier] > _TIER_RANK[c.tier]:
                problems.append(
                    _warning(
                        "tier-inversion",
                        f"{c.id} ({c.tier}) requires {req} ({by_id[req].tier})",
                        c.id,
                    )
                )

    edges = {
        cid: sorted({r for r in spec.requires if r in by_id and r != cid}) for cid, spec in by_id.items()
    }
    for cycle in find_cycles(edges):
        problems.append(_error("cycle", "cycle: " + " -> ".join(cycle), cycle[0]))

    used_areas = {c.area for c in concepts}
    for area_id in sorted(set(area_ids) - used_areas):
        problems.append(_warning("empty-area", f"area '{area_id}' has no concepts"))
    return problems


# ---------------------------------------------------------------- catalog


@dataclass(frozen=True)
class CatalogStats:
    concepts: int
    areas: int
    edges: int
    per_area: dict[str, int]
    per_tier: dict[str, int]
    max_depth: int
    """Longest chain of REQUIRES edges anywhere in the graph."""
    roots: int
    """Concepts with no prerequisites (where learning starts)."""
    leaves: int
    """Concepts no other concept requires (the most advanced ends)."""
    isolated: int
    """Concepts with neither prerequisites nor dependents."""


@dataclass
class Catalog:
    """A validated concept graph held in memory."""

    areas: list[AreaSpec]
    concepts: dict[str, ConceptSpec]
    problems: list[Problem] = field(default_factory=list)

    @classmethod
    def build(
        cls, areas: Sequence[AreaSpec], concepts: Sequence[ConceptSpec], *, strict: bool = True
    ) -> Catalog:
        """Validate and assemble. ``strict=False`` keeps an invalid catalog (for reporting stats)."""
        problems = validate(areas, concepts)
        if strict and any(p.severity == "error" for p in problems):
            raise CatalogError(problems)
        by_id: dict[str, ConceptSpec] = {}
        for spec in concepts:
            by_id.setdefault(spec.id, spec)  # first definition wins, as in the index
        return cls(areas=list(areas), concepts=by_id, problems=problems)

    @property
    def warnings(self) -> list[Problem]:
        return [p for p in self.problems if p.severity == "warning"]

    @property
    def is_valid(self) -> bool:
        return not any(p.severity == "error" for p in self.problems)

    def prerequisites_of(self, concept_id: str) -> list[str]:
        """Direct prerequisites in file order; dangling ids and self-loops dropped."""
        spec = self.concepts.get(concept_id)
        if spec is None:
            return []
        return [r for r in dict.fromkeys(spec.requires) if r in self.concepts and r != concept_id]

    def info(self, concept_id: str) -> ConceptInfo | None:
        spec = self.concepts.get(concept_id)
        if spec is None:
            return None
        return ConceptInfo(
            concept_id=spec.id,
            name=spec.name,
            area=spec.area,
            description=spec.description,
            aliases=list(spec.aliases),
            requires=self.prerequisites_of(spec.id),
        )

    def edges(self) -> list[tuple[str, str]]:
        return [(cid, req) for cid in self.concepts for req in self.prerequisites_of(cid)]

    @cached_property
    def index(self) -> ConceptIndex:
        return ConceptIndex(IndexEntry(c.id, c.name, tuple(c.aliases)) for c in self.concepts.values())

    def stats(self) -> CatalogStats:
        edges = self.edges()
        has_prereq = {src for src, _ in edges}
        required = {dst for _, dst in edges}
        return CatalogStats(
            concepts=len(self.concepts),
            areas=len(self.areas),
            edges=len(edges),
            per_area=dict(sorted(Counter(c.area for c in self.concepts.values()).items())),
            per_tier=dict(Counter(c.tier for c in self.concepts.values())),
            max_depth=self._max_depth(),
            roots=sum(1 for cid in self.concepts if cid not in has_prereq),
            leaves=sum(1 for cid in self.concepts if cid not in required),
            isolated=sum(1 for cid in self.concepts if cid not in has_prereq and cid not in required),
        )

    def _max_depth(self) -> int:
        # Longest path in a DAG: process concepts prerequisites-first (Kahn),
        # each one sitting one level above its deepest prerequisite.
        remaining = {cid: len(self.prerequisites_of(cid)) for cid in self.concepts}
        dependents: dict[str, list[str]] = {cid: [] for cid in self.concepts}
        for src, dst in self.edges():
            dependents[dst].append(src)
        depth = dict.fromkeys(self.concepts, 0)
        ready = deque(cid for cid, n in remaining.items() if n == 0)
        while ready:
            cid = ready.popleft()
            for dep in dependents[cid]:
                depth[dep] = max(depth[dep], depth[cid] + 1)
                remaining[dep] -= 1
                if remaining[dep] == 0:
                    ready.append(dep)
        return max(depth.values(), default=0)

    def to_payloads(self) -> tuple[dict[str, Any], dict[str, Any]]:
        """``(concepts.json, areas.json)`` payloads, in file order."""
        concepts = {"version": 1, "concepts": [c.model_dump() for c in self.concepts.values()]}
        areas = {"areas": [a.model_dump() for a in self.areas]}
        return concepts, areas


def default_areas_path(concepts_path: Path) -> Path:
    return concepts_path.with_name("areas.json")


def check_files(concepts_path: Path, areas_path: Path | None = None) -> tuple[Catalog | None, list[Problem]]:
    """Parse and validate both files without raising. Blocking file I/O.

    Returns every problem, and a best-effort catalog of the rows that parsed
    (``None`` only if a file is missing or is not JSON) so a report can still
    show progress statistics while the data has errors.
    """
    areas_path = areas_path or default_areas_path(concepts_path)
    problems: list[Problem] = []
    areas_payload = _read_json(areas_path, problems)
    concepts_payload = _read_json(concepts_path, problems)
    areas = parse_areas(areas_payload, problems) if areas_payload is not None else []
    concepts = parse_concepts(concepts_payload, problems) if concepts_payload is not None else []
    if areas_payload is None or concepts_payload is None:
        return None, problems
    catalog = Catalog.build(areas, concepts, strict=False)
    return catalog, problems + catalog.problems


def load_catalog(concepts_path: Path, areas_path: Path | None = None) -> Catalog:
    """Load and validate; raises ``CatalogError`` listing every error. Blocking file I/O."""
    catalog, problems = check_files(concepts_path, areas_path)
    if catalog is None or any(p.severity == "error" for p in problems):
        raise CatalogError(problems)
    return catalog


# ---------------------------------------------------------------- Neo4j


SCHEMA_STATEMENTS = (
    "CREATE CONSTRAINT concept_id_unique IF NOT EXISTS FOR (c:Concept) REQUIRE c.id IS UNIQUE",
    "CREATE CONSTRAINT user_id_unique IF NOT EXISTS FOR (u:User) REQUIRE u.id IS UNIQUE",
    "CREATE CONSTRAINT area_id_unique IF NOT EXISTS FOR (a:Area) REQUIRE a.id IS UNIQUE",
)


async def ensure_schema(driver: AsyncDriver, database: str | None = None) -> None:
    """Uniqueness constraints (each also creates the index MERGE and lookups use). Idempotent."""
    for statement in SCHEMA_STATEMENTS:
        await driver.execute_query(statement, database_=database)


@dataclass(frozen=True)
class SeedReport:
    areas: int
    concepts: int
    edges: int
    concepts_created: int
    concepts_changed: int
    concepts_retired: int
    edges_created: int
    edges_deleted: int
    areas_deleted: int

    @property
    def changed(self) -> bool:
        return any(
            (
                self.concepts_created,
                self.concepts_changed,
                self.concepts_retired,
                self.edges_created,
                self.edges_deleted,
                self.areas_deleted,
            )
        )


_UPSERT_AREAS = """
UNWIND $areas AS row
MERGE (a:Area {id: row.area_id})
SET a.name = row.name, a.description = row.description
"""

# The WHERE turns the SET into a no-op for unchanged rows, so the returned
# count is "concepts whose content changed" and a re-seed reports zero.
_UPSERT_CONCEPTS = """
UNWIND $concepts AS row
MERGE (c:Concept {id: row.id})
WITH c, row
WHERE c.name IS NULL OR c.name <> row.name
   OR coalesce(c.aliases, []) <> row.aliases
   OR coalesce(c.area, '') <> row.area
   OR coalesce(c.tier, '') <> row.tier
   OR coalesce(c.description, '') <> row.description
   OR coalesce(c.retired, false)
SET c.name = row.name, c.aliases = row.aliases, c.area = row.area,
    c.tier = row.tier, c.description = row.description, c.retired = false
RETURN count(c) AS changed
"""

# ``rank`` is the prerequisite's position in the file's ``requires`` list. BFS
# visits same-depth gaps in adjacency order, so storing it keeps the Neo4j and
# in-memory stores answering identically.
_MERGE_EDGES = """
UNWIND $edges AS e
MATCH (a:Concept {id: e[0]})
MATCH (b:Concept {id: e[1]})
MERGE (a)-[r:REQUIRES]->(b)
WITH r, e
WHERE r.rank IS NULL OR r.rank <> e[2]
SET r.rank = e[2]
"""

# Per source concept, drop REQUIRES edges the file no longer lists. Scoped to
# the concepts being seeded, so it never reaches into anything else.
_PRUNE_EDGES = """
UNWIND $concepts AS row
MATCH (a:Concept {id: row.id})-[r:REQUIRES]->(b:Concept)
WHERE NOT b.id IN row.requires
DELETE r
"""

# Concepts that left the file are retired, not deleted: DETACH DELETE would
# also delete readers' KNOWS edges, and a concept that returns (a rename
# reverted, a regenerated file) gets its readers' history back.
_RETIRE_CONCEPTS = """
MATCH (c:Concept)
WHERE NOT c.id IN $ids AND NOT coalesce(c.retired, false)
SET c.retired = true
RETURN count(c) AS retired
"""

_DROP_RETIRED_EDGES = """
MATCH (c:Concept)-[r:REQUIRES]-()
WHERE NOT c.id IN $ids
DELETE r
"""

_DROP_AREAS = """
MATCH (a:Area)
WHERE NOT a.id IN $area_ids
DETACH DELETE a
"""


async def seed_neo4j(
    driver: AsyncDriver,
    catalog: Catalog,
    *,
    retire_missing: bool = True,
    dry_run: bool = False,
    database: str | None = None,
) -> SeedReport:
    """Make Neo4j's concept graph match ``catalog``. Idempotent; never touches User or KNOWS.

    Everything runs in one write transaction, so a failure leaves the previous
    graph intact rather than half-replaced. ``retire_missing=False`` limits the
    run to the catalog's own concepts and areas (tests seed a namespaced
    fixture next to real data that way). ``dry_run`` executes the same
    statements and rolls back, so its counts are exactly what a real run would
    change.
    """
    if not catalog.is_valid:
        raise CatalogError(catalog.problems)
    if not dry_run:
        await ensure_schema(driver, database)
    concepts_payload, areas_payload = catalog.to_payloads()
    concept_rows = [
        {
            "id": c["id"],
            "name": c["name"],
            "aliases": c["aliases"],
            "area": c["area"],
            "tier": c["tier"],
            "description": c["description"],
            "requires": list(dict.fromkeys(c["requires"])),
        }
        for c in concepts_payload["concepts"]
    ]
    params: dict[str, Any] = {
        "areas": areas_payload["areas"],
        "concepts": concept_rows,
        "edges": [[row["id"], req, rank] for row in concept_rows for rank, req in enumerate(row["requires"])],
        "ids": list(catalog.concepts),
        "area_ids": [a.area_id for a in catalog.areas],
    }

    async def work(tx: AsyncManagedTransaction | AsyncTransaction) -> SeedReport:
        await (await tx.run(_UPSERT_AREAS, params)).consume()
        upsert = await tx.run(_UPSERT_CONCEPTS, params)
        changed = (await upsert.single(strict=True))["changed"]
        created = (await upsert.consume()).counters.nodes_created
        edges_created = (await (await tx.run(_MERGE_EDGES, params)).consume()).counters.relationships_created
        edges_deleted = (await (await tx.run(_PRUNE_EDGES, params)).consume()).counters.relationships_deleted
        retired = areas_deleted = 0
        if retire_missing:
            retire = await tx.run(_RETIRE_CONCEPTS, params)
            retired = (await retire.single(strict=True))["retired"]
            await retire.consume()
            dropped = await (await tx.run(_DROP_RETIRED_EDGES, params)).consume()
            edges_deleted += dropped.counters.relationships_deleted
            areas_deleted = (await (await tx.run(_DROP_AREAS, params)).consume()).counters.nodes_deleted
        return SeedReport(
            areas=len(catalog.areas),
            concepts=len(catalog.concepts),
            edges=len(params["edges"]),
            concepts_created=created,
            concepts_changed=changed - created,
            concepts_retired=retired,
            edges_created=edges_created,
            edges_deleted=edges_deleted,
            areas_deleted=areas_deleted,
        )

    async with driver.session(database=database) as session:
        if not dry_run:
            return await session.execute_write(work)
        tx = await session.begin_transaction()
        try:
            return await work(tx)
        finally:
            await tx.rollback()
