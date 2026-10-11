"""Validate the concept seed files and load them into Neo4j.

    .venv/bin/python scripts/seed_graph.py              # validate, then seed
    .venv/bin/python scripts/seed_graph.py --dry-run    # report what would change, write nothing

Safe to re-run: concepts and REQUIRES edges are MERGEd, edges no longer in the
file are deleted, concepts no longer in the file are retired (kept, with their
edges removed) so readers' KNOWS edges survive. User nodes and KNOWS edges are
never touched. A running prereq_graph server keeps its resolution index until
it restarts.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from papermind.config import get_settings
from papermind.graph.seed import check_files, default_areas_path, seed_neo4j
from papermind.graph.store import create_driver


async def run(concepts: Path, areas: Path, *, dry_run: bool, keep_missing: bool) -> int:
    catalog, problems = check_files(concepts, areas)
    errors = [p for p in problems if p.severity == "error"]
    for problem in sorted(problems, key=lambda p: (p.severity != "error", p.code, p.message)):
        print(problem)
    if catalog is None or errors:
        print(f"\nNot seeding: {len(errors)} error(s). Run scripts/validate_concepts.py for details.")
        return 1

    settings = get_settings()
    driver = create_driver(settings, timeout_s=10.0)
    try:
        await driver.verify_connectivity()
        report = await seed_neo4j(driver, catalog, retire_missing=not keep_missing, dry_run=dry_run)
    finally:
        await driver.close()

    verb = "would change" if dry_run else "changed"
    print(
        f"\n{'Dry run' if dry_run else 'Seeded'} {settings.neo4j_uri}: "
        f"{report.concepts} concepts, {report.edges} edges, {report.areas} areas"
    )
    print(f"  concepts created  {report.concepts_created}")
    print(f"  concepts updated  {report.concepts_changed}")
    print(f"  concepts retired  {report.concepts_retired}")
    print(f"  edges created     {report.edges_created}")
    print(f"  edges deleted     {report.edges_deleted}")
    print(f"  areas deleted     {report.areas_deleted}")
    print(f"  -> {verb} the graph" if report.changed else "  -> already up to date")
    return 0


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--concepts", type=Path, default=settings.concepts_path)
    parser.add_argument("--areas", type=Path, default=None, help="default: areas.json next to --concepts")
    parser.add_argument("--dry-run", action="store_true", help="run inside a transaction and roll back")
    parser.add_argument(
        "--keep-missing",
        action="store_true",
        help="do not retire concepts/areas that are in Neo4j but not in the file",
    )
    args = parser.parse_args(argv)
    areas = args.areas or default_areas_path(args.concepts)
    return asyncio.run(run(args.concepts, areas, dry_run=args.dry_run, keep_missing=args.keep_missing))


if __name__ == "__main__":
    sys.exit(main())
