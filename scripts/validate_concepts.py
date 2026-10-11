"""Validate the concept seed files and print graph statistics.

    .venv/bin/python scripts/validate_concepts.py
    .venv/bin/python scripts/validate_concepts.py --concepts path/to/concepts.json --strict

Prints every problem (errors first), then statistics. Exit status: 0 when the
data is valid, 1 when there are errors (or warnings, with --strict), 2 when a
file is missing or is not JSON.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Scripts run as files from a checkout that is not pip-installed; make the
# repository root importable the same way `python -m` would.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from papermind.config import get_settings
from papermind.graph.seed import Catalog, Problem, check_files, default_areas_path


def format_stats(catalog: Catalog) -> str:
    stats = catalog.stats()
    names = {a.area_id: a.name for a in catalog.areas}
    width = max((len(a) for a in stats.per_area), default=10)
    lines = [
        "Stats",
        f"  concepts   {stats.concepts}",
        f"  areas      {stats.areas}",
        f"  edges      {stats.edges}  (REQUIRES)",
        f"  max depth  {stats.max_depth}  (longest prerequisite chain)",
        f"  roots      {stats.roots}  (no prerequisites)",
        f"  leaves     {stats.leaves}  (nothing requires them)",
        f"  isolated   {stats.isolated}",
        "  per tier   "
        + ", ".join(f"{tier} {stats.per_tier.get(tier, 0)}" for tier in ("foundation", "core", "advanced")),
        "  per area",
    ]
    for area_id, count in stats.per_area.items():
        lines.append(f"    {area_id:<{width}}  {count:4}  {names.get(area_id, '(unknown area)')}")
    return "\n".join(lines)


def report(problems: list[Problem]) -> str:
    ordered = sorted(problems, key=lambda p: (p.severity != "error", p.code, p.message))
    return "\n".join(str(p) for p in ordered)


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--concepts", type=Path, default=settings.concepts_path)
    parser.add_argument("--areas", type=Path, default=None, help="default: areas.json next to --concepts")
    parser.add_argument("--strict", action="store_true", help="treat warnings as errors")
    args = parser.parse_args(argv)

    areas_path = args.areas or default_areas_path(args.concepts)
    print(f"concepts: {args.concepts}\nareas:    {areas_path}\n")
    catalog, problems = check_files(args.concepts, areas_path)
    errors = sum(p.severity == "error" for p in problems)
    warnings = len(problems) - errors

    if problems:
        print(report(problems) + "\n")
    if catalog is None:
        print(f"Result: cannot read the seed files ({errors} error(s))")
        return 2
    print(format_stats(catalog))
    if errors:
        print("  (statistics are best-effort while errors remain; cycles are excluded from max depth)")
    print(f"\nResult: {errors} error(s), {warnings} warning(s)")
    return 1 if errors or (args.strict and warnings) else 0


if __name__ == "__main__":
    sys.exit(main())
