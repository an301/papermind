"""Summaries and comparison tables, computed from result rows (dicts) so any saved run can be re-rendered.

    .venv/bin/python -m evals.harness.report evals/results/<run>.json
    .venv/bin/python -m evals.harness.report <baseline>.json <optimized>.json   # latency A/B
    .venv/bin/python -m evals.harness.report --latest latency                   # newest of each arm

Quality is always sliced by the level the reader asked for, not only
aggregated: a writer that nails expert answers and patronises beginners scores
fine overall and fails the readers the product is for.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from evals.harness import scorers
from evals.harness.dataset import LEVELS, RESULTS_DIR

Row = Mapping[str, Any]

# ---------------------------------------------------------------- summaries


def _sum(rows: Sequence[Row], key: str) -> int:
    return sum(int(r.get(key) or 0) for r in rows)


def quality(rows: Sequence[Row]) -> dict[str, Any]:
    """Answer quality over ``rows`` (already restricted to answered, non-error turns)."""
    return {
        "n": len(rows),
        "recall_writer": scorers.mean(r.get("recall_writer") for r in rows),
        "recall_first5": scorers.mean(r.get("recall_first5") for r in rows),
        "recall_union": scorers.mean(r.get("recall_union") for r in rows),
        # Macro: the mean per-answer score, what a typical answer looks like.
        "faithfulness": scorers.mean(r.get("faithfulness") for r in rows),
        # Micro: every claim weighted equally, so long answers count for more.
        "faithfulness_micro": scorers.ratio(_sum(rows, "claims_supported"), _sum(rows, "claims_total")),
        "f1": scorers.mean(r.get("f1") for r in rows),
        "calibration": scorers.calibration_accuracy(
            [r.get("calibration_label") for r in rows], [r.get("level") for r in rows]
        ),
        "citation_precision": scorers.ratio(_sum(rows, "citations_supported"), _sum(rows, "citations_total")),
        "uncited_rate": scorers.ratio(
            _sum(rows, "paragraphs_uncited"), _sum(rows, "paragraphs_paper_derived")
        ),
    }


def _gate(value: float | None, threshold: float, *, at_least: bool) -> dict[str, Any]:
    passed = None if value is None else (value >= threshold if at_least else value < threshold)
    return {"value": value, "threshold": threshold, "pass": passed}


def summarize_agent(rows: Sequence[Row]) -> dict[str, Any]:
    """Everything the agent and latency suites report, from per-row result dicts."""
    ok = [r for r in rows if r.get("ok")]
    by_level = {lvl: quality([r for r in ok if r.get("level") == lvl]) for lvl in LEVELS}
    labels = [r.get("calibration_label") for r in ok]
    targets = [r.get("level") for r in ok]
    overall = quality(ok)
    return {
        "n": len(rows),
        "answered": len(ok),
        "overall": overall,
        "by_level": by_level,
        "by_origin": {o: quality(g) for o, g in scorers.slice_by(ok, "origin").items()},
        "calibration": {
            "accuracy": overall["calibration"],
            "per_level": scorers.per_level_accuracy(labels, targets),
            "confusion": scorers.confusion_matrix(labels, targets),
        },
        "gates": {
            "citation_precision": {
                **_gate(overall["citation_precision"], scorers.CITATION_PRECISION_GATE, at_least=True),
                "by_level": {lvl: q["citation_precision"] for lvl, q in by_level.items()},
            },
            "uncited_rate": {
                **_gate(overall["uncited_rate"], scorers.UNCITED_RATE_GATE, at_least=False),
                "by_level": {lvl: q["uncited_rate"] for lvl, q in by_level.items()},
            },
        },
        # Latency over answered turns only: a turn that failed fast is not a fast answer.
        "latency": {
            "turn_ms": scorers.latency_summary(r.get("latency_ms") for r in ok),
            "ttft_ms": scorers.latency_summary(r.get("ttft_ms") for r in ok),
            "answer_ms": scorers.latency_summary(r.get("answer_ms") for r in ok),
            "by_level_turn_ms": {
                lvl: scorers.latency_summary(r.get("latency_ms") for r in ok if r.get("level") == lvl)
                for lvl in LEVELS
            },
        },
        # Cost over every row: failed turns were billed too.
        "cost": scorers.cost_summary((r.get("usage") or {}).get("cost_usd") for r in rows),
        "judge_cost": scorers.cost_summary(r.get("judge_cost_usd") for r in rows if r.get("judgement")),
        "cache": scorers.cache_summary(r.get("usage") or {} for r in rows),
        "calls": {
            "model_calls_p50": _p50((r.get("usage") or {}).get("model_calls") for r in ok),
            "tool_calls_p50": _p50((r.get("usage") or {}).get("tool_calls") for r in ok),
            "tool_retries": sum(int((r.get("usage") or {}).get("tool_retries") or 0) for r in rows),
        },
        "errors": {
            "n": len(rows) - len(ok),
            "by_code": dict(
                Counter(str(r.get("error_code") or "empty_answer") for r in rows if not r.get("ok"))
            ),
            "judge_errors": sum(len(r.get("judge_errors") or []) for r in rows),
        },
    }


def _p50(values: Any) -> float | None:
    present = [float(v) for v in values if v is not None]
    return scorers.percentile(present, 50) if present else None


# ---------------------------------------------------------------- formatting


def fmt(value: Any, kind: str = "pct") -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "PASS" if value else "FAIL"
    if kind == "pct":
        return f"{100 * value:.1f}%"
    if kind == "ms":
        return f"{value / 1000:.2f}s" if value >= 1000 else f"{value:.0f}ms"
    if kind == "usd":
        return f"${value:.4f}"
    if kind == "num":
        return f"{value:.3f}" if isinstance(value, float) else str(value)
    if kind == "count":
        return f"{value:g}"
    return str(value)


def table(headers: Sequence[str], rows: Sequence[Sequence[Any]], title: str | None = None) -> str:
    cells = [[str(h) for h in headers], *[[str(c) for c in row] for row in rows]]
    widths = [max(len(r[i]) for r in cells) for i in range(len(headers))]

    def line(row: Sequence[str]) -> str:
        first = row[0].ljust(widths[0])
        rest = [c.rjust(w) for c, w in zip(row[1:], widths[1:], strict=True)]
        return "  ".join([first, *rest]).rstrip()

    out = [title] if title else []
    out += [line(cells[0]), "  ".join("-" * w for w in widths)]
    out += [line(r) for r in cells[1:]]
    return "\n".join(out)


# ---------------------------------------------------------------- retrieval


def retrieval_tables(summary: Mapping[str, Any]) -> str:
    modes, ks = summary["modes"], summary["ks"]
    metrics = [f"recall@{k}" for k in ks] + [f"mrr@{max(ks)}"]
    parts = [
        table(
            ["mode", *metrics, "n"],
            [
                [
                    m,
                    *(
                        fmt(summary["overall"][m][x], "num" if x.startswith("mrr") else "pct")
                        for x in metrics
                    ),
                    summary["overall"][m]["n"],
                ]
                for m in modes
            ],
            title=f"Retrieval ablation ({summary['n']} questions)",
        )
    ]
    k0 = f"recall@{ks[0]}"
    for key, label in (
        ("by_origin", "origin"),
        ("by_level", "level"),
        ("by_query_type", "query type"),
        ("by_paper", "paper"),
    ):
        groups = summary.get(key) or {}
        if not groups:
            continue
        rows = [
            [g, *(fmt(cell[m][k0]) for m in modes), next(iter(cell.values()))["n"]]
            for g, cell in groups.items()
        ]
        parts.append(table([label, *(f"{m} {k0}" for m in modes), "n"], rows, title=f"{k0} by {label}"))
    head = summary.get("headline") or {}
    parts.append(f"Headline: {head.get('metric')} = {fmt(head.get('value'))}")
    return "\n\n".join(parts)


# ---------------------------------------------------------------- agent


QUALITY_COLUMNS: tuple[tuple[str, str], ...] = (
    ("recall_writer", "recall(writer)"),
    ("recall_first5", "recall(1st@5)"),
    ("recall_union", "recall(union)"),
    ("faithfulness", "faithful"),
    ("f1", "F1"),
    ("calibration", "calibrated"),
    ("citation_precision", "cite prec"),
    ("uncited_rate", "uncited"),
)


def agent_tables(summary: Mapping[str, Any]) -> str:
    slices = [("all", summary["overall"]), *summary["by_level"].items()]
    rows = [[name, q["n"], *(fmt(q[key]) for key, _ in QUALITY_COLUMNS)] for name, q in slices]
    parts = [
        table(
            ["level", "n", *(label for _, label in QUALITY_COLUMNS)],
            rows,
            title=f"Answer quality by level ({summary['answered']}/{summary['n']} answered)",
        )
    ]
    if len(summary.get("by_origin") or {}) > 1:
        rows = [
            [o, q["n"], *(fmt(q[key]) for key, _ in QUALITY_COLUMNS)] for o, q in summary["by_origin"].items()
        ]
        parts.append(
            table(["origin", "n", *(label for _, label in QUALITY_COLUMNS)], rows, title="By origin")
        )
    parts.append(calibration_table(summary["calibration"]))
    gates = summary["gates"]
    cp, un = gates["citation_precision"], gates["uncited_rate"]
    parts.append(
        table(
            ["gate", "value", "threshold", *LEVELS, "result"],
            [
                [
                    "citation precision",
                    fmt(cp["value"]),
                    f">= {fmt(cp['threshold'])}",
                    *(fmt(cp["by_level"][lvl]) for lvl in LEVELS),
                    fmt(cp["pass"]),
                ],
                [
                    "uncited paper paragraphs",
                    fmt(un["value"]),
                    f"< {fmt(un['threshold'])}",
                    *(fmt(un["by_level"][lvl]) for lvl in LEVELS),
                    fmt(un["pass"]),
                ],
            ],
            title="Design gates (DESIGN.md A13)",
        )
    )
    parts.append(latency_cost_table(summary))
    errors = summary["errors"]
    if errors["n"] or errors["judge_errors"]:
        parts.append(
            f"Errors: {errors['n']} turns {errors['by_code']}; judge errors: {errors['judge_errors']}"
        )
    return "\n\n".join(parts)


def calibration_table(calibration: Mapping[str, Any]) -> str:
    confusion = calibration["confusion"]
    per_level = calibration["per_level"]
    rows = [
        [
            f"asked {t}",
            *(confusion[t].get(lvl, 0) for lvl in LEVELS),
            fmt(per_level.get(t, {}).get("accuracy")),
        ]
        for t in LEVELS
        if t in confusion
    ]
    return table(
        ["", *(f"judged {lvl}" for lvl in LEVELS), "accuracy"],
        rows,
        title=f"Calibration (blind judge): {fmt(calibration['accuracy'])} overall",
    )


def latency_cost_table(summary: Mapping[str, Any]) -> str:
    lat, cost, cache = summary["latency"], summary["cost"], summary["cache"]
    rows = [
        [name, fmt(lat[key]["p50"], "ms"), fmt(lat[key]["p95"], "ms"), lat[key]["n"]]
        for key, name in (
            ("turn_ms", "turn"),
            ("ttft_ms", "first answer token"),
            ("answer_ms", "answer done"),
        )
    ]
    out = table(["latency", "p50", "p95", "n"], rows, title="Latency (p50/p95, never the mean)")
    judge = summary.get("judge_cost") or {}
    judged = f"; judges {fmt(judge.get('total_usd'), 'usd')}" if judge.get("n") else ""
    out += (
        f"\nCost: {fmt(cost['total_usd'], 'usd')} total, {fmt(cost['mean_usd'], 'usd')}/query "
        f"(p95 {fmt(cost['p95_usd'], 'usd')}){judged}"
        f"\nCache: {fmt(cache['cache_hit_rate'])} of prompt tokens read from cache "
        f"({cache['cache_read_input_tokens']} read, {cache['cache_creation_input_tokens']} written)"
    )
    return out


# ---------------------------------------------------------------- latency A/B


def latency_tables(summary: Mapping[str, Any]) -> str:
    """A latency run has no judges: latency, cost and cache, plus the judge-free quality numbers."""
    calls = summary["calls"]
    slices = [("all", summary["overall"]), *summary["by_level"].items()]
    turn_by_level = summary["latency"]["by_level_turn_ms"]
    rows = [
        [
            name,
            q["n"],
            fmt(q["recall_writer"]),
            fmt(q["f1"]),
            fmt((summary["latency"]["turn_ms"] if name == "all" else turn_by_level[name])["p50"], "ms"),
        ]
        for name, q in slices
    ]
    parts = [
        latency_cost_table(summary),
        table(["level", "n", "recall(writer)", "F1", "turn p50"], rows, title="By level"),
        f"Per turn (p50): {fmt(calls['model_calls_p50'], 'count')} model calls, "
        f"{fmt(calls['tool_calls_p50'], 'count')} tool calls; {calls['tool_retries']} tool retries in total",
    ]
    errors = summary["errors"]
    if errors["n"]:
        parts.append(f"Errors: {errors['n']} turns {errors['by_code']}")
    return "\n\n".join(parts)


def _rel(a: float | None, b: float | None) -> str:
    """(A - B) / A: the fraction of the baseline the optimisation removed."""
    if a is None or b is None or a == 0:
        return "-"
    return f"{100 * (a - b) / a:+.1f}%"


def latency_ab_table(baseline: Mapping[str, Any], optimized: Mapping[str, Any]) -> str:
    a, b = baseline["summary"], optimized["summary"]
    rows: list[list[Any]] = []
    for key, name in (("turn_ms", "turn"), ("ttft_ms", "first token"), ("answer_ms", "answer done")):
        for p in ("p50", "p95"):
            va, vb = a["latency"][key][p], b["latency"][key][p]
            rows.append([f"{name} {p}", fmt(va, "ms"), fmt(vb, "ms"), _rel(va, vb)])
    ca, cb = a["cost"]["mean_usd"], b["cost"]["mean_usd"]
    rows.append(["cost / query", fmt(ca, "usd"), fmt(cb, "usd"), _rel(ca, cb)])
    rows.append(["cache hit rate", fmt(a["cache"]["cache_hit_rate"]), fmt(b["cache"]["cache_hit_rate"]), ""])
    rows.append(["answered", f"{a['answered']}/{a['n']}", f"{b['answered']}/{b['n']}", ""])
    same = [r["id"] for r in baseline["rows"]] == [r["id"] for r in optimized["rows"]]
    note = (
        ""
        if same
        else "\nWARNING: the two runs answered different questions; the comparison is not like for like."
    )
    return (
        table(
            ["metric", "A baseline", "B optimized", "(A-B)/A"],
            rows,
            title="Latency A/B: A = sequential tools, no caching; B = parallel tools + prompt caching",
        )
        + note
    )


# ---------------------------------------------------------------- agreement


def agreement_tables(summary: Mapping[str, Any]) -> str:
    agreement = summary["agreement"]
    head = (
        f"Judge vs human ({agreement['n']} answers): agreement {fmt(agreement['agreement'])}, "
        f"Cohen's kappa {fmt(agreement['kappa'], 'num')}, linear-weighted kappa {fmt(agreement['kappa_linear'], 'num')}"
    )
    confusion = agreement["confusion"]
    rows = [[f"human {h}", *(confusion[h].get(j, 0) for j in confusion)] for h in confusion]
    parts = [head, table(["", *(f"judge {j}" for j in confusion)], rows)]
    if summary.get("stale"):
        parts.append(
            f"Skipped {len(summary['stale'])} labels whose answer_text_hash no longer matches: {summary['stale'][:10]}"
        )
    if summary.get("unmatched"):
        parts.append(
            f"Skipped {len(summary['unmatched'])} labels with no answer in the results: {summary['unmatched'][:10]}"
        )
    return "\n\n".join(parts)


# ---------------------------------------------------------------- dispatch + CLI


def render(result: Mapping[str, Any]) -> str:
    suite = result.get("suite")
    if suite == "retrieval":
        return retrieval_tables(result["summary"])
    if suite == "agent":
        return f"[agent]\n{agent_tables(result['summary'])}"
    if suite == "latency":
        return f"[latency {result.get('config', '')}]\n{latency_tables(result['summary'])}"
    if suite == "agreement":
        return agreement_tables(result["summary"])
    raise ValueError(f"unknown suite {suite!r}")


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def latest(pattern: str, results_dir: Path = RESULTS_DIR) -> Path | None:
    found = sorted(results_dir.glob(pattern))
    return found[-1] if found else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--latest", choices=["retrieval", "agent", "latency", "agreement"])
    args = parser.parse_args(argv)
    paths = list(args.paths)
    if args.latest == "latency":
        paths = [p for p in (latest("*-latency-baseline.json"), latest("*-latency-optimized.json")) if p]
    elif args.latest:
        paths = [p for p in [latest(f"*-{args.latest}.json")] if p]
    if not paths:
        print("no result files given or found", file=sys.stderr)
        return 1
    results = [load(p) for p in paths]
    if len(results) == 2 and all(r.get("suite") == "latency" for r in results):
        a, b = sorted(results, key=lambda r: r.get("config") != "baseline")
        print(latency_ab_table(a, b))
        return 0
    for result in results:
        print(render(result))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
