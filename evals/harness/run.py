"""Eval CLI: loop over the benchmark, run the system, score, log, save.

    # retrieval ablation, no API key, seconds
    .venv/bin/python -m evals.harness.run --suite retrieval --modes bm25,dense,hybrid --k 5,10 [--origin natural]

    # full agent: recall, faithfulness, F1, calibration, citation gates, latency, cost (spends money)
    .venv/bin/python -m evals.harness.run --suite agent [--limit 24] [--papers 1706.03762 2305.18290]

    # latency A/B: same questions, sequential, each switch pair off vs on (spends money)
    .venv/bin/python -m evals.harness.run --suite latency --config baseline --limit 24
    .venv/bin/python -m evals.harness.run --suite latency --config optimized --limit 24

    # calibration judge vs hand labels (prepare a blind sheet first, then fill in hand_labels.csv)
    .venv/bin/python -m evals.harness.run --suite agreement --prepare 30
    .venv/bin/python -m evals.harness.run --suite agreement --labels evals/benchmark/hand_labels.csv

Results go to evals/results/<timestamp>-<suite>[-<variant>].json (variant: the
latency config, or the origin/papers filter) and a compact table is printed.
Re-render any saved run with ``python -m evals.harness.report <file>``.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import random
import subprocess
import sys
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals.harness import report, scorers
from evals.harness.dataset import (
    DEFAULT_BENCHMARK,
    DEFAULT_HAND_LABELS,
    LEVELS,
    RESULTS_DIR,
    Benchmark,
    BenchmarkError,
    load_benchmark,
    select_rows,
)
from evals.harness.wandb_log import WandbLogger, settings_snapshot
from papermind.config import REPO_ROOT, Settings, get_settings

log = logging.getLogger("evals")

HAND_LABEL_COLUMNS = ("id", "answer_text_hash", "human_level", "notes")


def _csv(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--suite", required=True, choices=["retrieval", "agent", "latency", "agreement", "writer"])
    p.add_argument("--benchmark", type=Path, default=DEFAULT_BENCHMARK)
    p.add_argument("--origin", choices=["generated", "natural"])
    p.add_argument("--papers", nargs="+", help="paper ids (space- or comma-separated)")
    p.add_argument("--limit", type=int, help="a balanced, deterministic subset of N rows")
    p.add_argument(
        "--modes", type=_csv, default=["bm25", "dense", "hybrid"], help="retrieval: comma-separated"
    )
    p.add_argument(
        "--k", type=lambda v: [int(x) for x in _csv(v)], default=[5, 10], help="retrieval: e.g. 5,10"
    )
    p.add_argument("--config", choices=["baseline", "optimized"], help="latency: which arm of the A/B")
    p.add_argument("--concurrency", type=int, default=4, help="agent: rows in flight at once")
    p.add_argument(
        "--no-judges", action="store_true", help="agent: skip the LLM judges (recall, F1, latency, cost only)"
    )
    p.add_argument("--labels", type=Path, default=DEFAULT_HAND_LABELS, help="agreement: the hand labels CSV")
    p.add_argument("--results", type=Path, help="agreement: agent results to compare (default: the newest)")
    p.add_argument(
        "--rejudge",
        action="store_true",
        help="agreement: re-run the level judge instead of reusing its stored label",
    )
    p.add_argument(
        "--prepare", type=int, metavar="N", help="agreement: write a blind labelling sheet of N answers"
    )
    p.add_argument(
        "--writer", choices=["claude", "mistral"], default="claude", help="writer: which writer to run"
    )
    p.add_argument(
        "--mistral-model",
        help="writer: the vLLM model name (the LoRA module for the tuned writer, the base id for the baseline)",
    )
    p.add_argument("--out-dir", type=Path, default=RESULTS_DIR)
    p.add_argument("--no-wandb", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    if args.papers:
        args.papers = [x for item in args.papers for x in _csv(item)]
    if args.suite == "latency" and args.config is None:
        p.error("--suite latency needs --config baseline|optimized")
    if args.limit is not None and args.limit < 1:
        p.error("--limit must be positive")
    return args


# ---------------------------------------------------------------- shared plumbing


def _git_commit() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=5
        )
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _variant(args: argparse.Namespace) -> str:
    parts: list[str] = []
    if args.suite == "latency":
        parts.append(args.config)
    if args.origin:
        parts.append(args.origin)
    if args.papers:
        parts.append("+".join(args.papers) if len(args.papers) <= 2 else f"{len(args.papers)}papers")
    if args.limit:
        parts.append(f"n{args.limit}")
    return "-".join(parts)


def result_path(out_dir: Path, suite: str, variant: str, stamp: str) -> Path:
    name = f"{stamp}-{suite}" + (f"-{variant}" if variant else "")
    path = out_dir / f"{name}.json"
    n = 2
    while path.exists():
        path = out_dir / f"{name}-{n}.json"
        n += 1
    return path


def save(result: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8")


def load_rows(args: argparse.Namespace) -> Benchmark:
    try:
        rows = load_benchmark(args.benchmark, origin=args.origin, papers=args.papers)
    except FileNotFoundError:
        raise SystemExit(f"No benchmark at {args.benchmark}.") from None
    except BenchmarkError as exc:
        raise SystemExit(str(exc)) from None
    if not rows:
        raise SystemExit("No benchmark rows match the filters.")
    return Benchmark(args.benchmark, select_rows(rows, args.limit))


def envelope(args: argparse.Namespace, settings: Settings, started: datetime, **extra: Any) -> dict[str, Any]:
    return {
        "suite": args.suite,
        **({"config": args.config} if args.suite == "latency" else {}),
        "started_at": started.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "git_commit": _git_commit(),
        "args": {k: (str(v) if isinstance(v, Path) else v) for k, v in vars(args).items()},
        "settings": settings_snapshot(settings),
        **extra,
    }


def finish(
    args: argparse.Namespace,
    settings: Settings,
    result: dict[str, Any],
    stamp: str,
    table_rows: list[dict[str, Any]],
) -> Path:
    path = result_path(args.out_dir, args.suite, _variant(args), stamp)
    save(result, path)
    print(report.render(result))
    logger = WandbLogger(
        args.suite,
        settings=settings,
        config={
            "suite": args.suite,
            "args": result["args"],
            "benchmark": result.get("benchmark"),
            **result["settings"],
        },
        name=path.stem,
        enabled=not args.no_wandb,
    )
    logger.log_summary(result["summary"])
    logger.log_table("rows", table_rows)
    where = logger.finish()
    print(f"\nSaved {path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path}")
    if where:
        print(f"W&B ({settings.wandb_mode}): {where}")
    return path


# ---------------------------------------------------------------- suites


async def suite_retrieval(args: argparse.Namespace, settings: Settings, stamp: str, started: datetime) -> int:
    from evals.harness.retrieval import run_retrieval, summarize_retrieval

    bench = load_rows(args)
    t0 = time.perf_counter()
    results = await run_retrieval(bench.rows, modes=args.modes, ks=args.k)
    elapsed = time.perf_counter() - t0
    summary = summarize_retrieval(results, args.modes, args.k)
    result = envelope(
        args,
        settings,
        started,
        benchmark=bench.describe(),
        duration_s=round(elapsed, 2),
        summary=summary,
        rows=[r.to_dict() for r in results],
    )
    finish(args, settings, result, stamp, [r.flat() for r in results])
    print(f"{len(results)} questions x {len(args.modes)} modes in {elapsed:.1f}s")
    return 0


def _progress(total: int) -> Any:
    done = 0
    t0 = time.perf_counter()

    def report_row(r: Any) -> None:
        nonlocal done
        done += 1
        status = "ok" if r.ok else f"ERROR {r.error_code}"
        bits = [
            f"[{done}/{total} {time.perf_counter() - t0:5.0f}s]",
            r.id,
            r.level,
            status,
            f"{(r.latency_ms or 0) / 1000:.1f}s",
            f"${(r.usage or {}).get('cost_usd', 0):.4f}",
            f"recall={r.recall_writer:.0f}" if r.recall_writer is not None else "recall=-",
        ]
        if r.judgement is not None:
            bits += [
                f"faithful={report.fmt(r.faithfulness)}",
                f"judged={r.calibration_label or '-'}",
                f"cites={r.citations_supported}/{r.citations_total}",
            ]
        print("  " + " ".join(bits), flush=True)

    return report_row


async def suite_agent(args: argparse.Namespace, settings: Settings, stamp: str, started: datetime) -> int:
    from evals.harness.runner import AgentHarness, latency_settings

    if not settings.has_api_key:
        print("The agent suites call the model and need ANTHROPIC_API_KEY in .env.", file=sys.stderr)
        return 2
    bench = load_rows(args)
    latency = args.suite == "latency"
    if latency:
        settings = latency_settings(settings, args.config)
    # Latency runs one question at a time so turns do not contend for the API,
    # the MCP servers, or the CPU-bound retriever.
    concurrency = 1 if latency else args.concurrency
    judge = not (latency or args.no_judges)
    print(
        f"{args.suite}: {len(bench.rows)} questions, concurrency {concurrency}, judges {'on' if judge else 'off'}, "
        f"parallel_tools={settings.parallel_tools} prompt_caching={settings.prompt_caching}. This spends API money."
    )
    t0 = time.perf_counter()
    async with AgentHarness(settings, concurrency=concurrency, judge=judge) as harness:
        results = await harness.run_rows(bench.rows, on_done=_progress(len(bench.rows)))
        readers = {
            lvl: {"user_id": r.user_id, "known_concepts": len(r.known)} for lvl, r in harness.readers.items()
        }
    elapsed = time.perf_counter() - t0
    rows = [r.to_dict() for r in results]
    summary = report.summarize_agent(rows)
    result = envelope(
        args,
        settings,
        started,
        benchmark=bench.describe(),
        duration_s=round(elapsed, 1),
        readers=readers,
        recall_headline="recall_writer: gold chunk among the passages handed to the writer",
        summary=summary,
        rows=rows,
    )
    finish(args, settings, result, stamp, [_table_row(r) for r in rows])
    if latency:
        other = "optimized" if args.config == "baseline" else "baseline"
        counterpart = report.latest(f"*-latency-{other}*.json", args.out_dir)
        if counterpart is not None:
            theirs = report.load(counterpart)
            a, b = (result, theirs) if args.config == "baseline" else (theirs, result)
            print("\n" + report.latency_ab_table(a, b) + f"\n(vs {counterpart.name})")
    return 0


def _table_row(r: dict[str, Any]) -> dict[str, Any]:
    usage = r.get("usage") or {}
    return {
        "id": r["id"],
        "paper_id": r["paper_id"],
        "level": r["level"],
        "origin": r["origin"],
        "query_type": r["query_type"],
        "ok": r["ok"],
        "recall_writer": r["recall_writer"],
        "recall_first5": r["recall_first5"],
        "recall_union": r["recall_union"],
        "faithfulness": r["faithfulness"],
        "f1": r["f1"],
        "calibration_label": r["calibration_label"],
        "citation_precision": r["citation_precision"],
        "paragraphs_uncited": r["paragraphs_uncited"],
        "latency_ms": r["latency_ms"],
        "ttft_ms": r["ttft_ms"],
        "cost_usd": usage.get("cost_usd"),
        "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
        "error_code": r["error_code"],
        "question": r["question"],
        "answer": r["answer"],
    }


# ---------------------------------------------------------------- agreement


def read_hand_labels(path: Path) -> list[dict[str, str]]:
    """Filled-in rows of the hand-label CSV (blank human_level rows are templates and are skipped)."""
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in HAND_LABEL_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise SystemExit(f"{path}: missing columns {missing}; expected {list(HAND_LABEL_COLUMNS)}")
        rows = [{k: (v or "").strip() for k, v in row.items() if k} for row in reader]
    labelled = [r for r in rows if r.get("human_level")]
    bad = [r["id"] for r in labelled if r["human_level"] not in LEVELS]
    if bad:
        raise SystemExit(f"{path}: human_level must be one of {list(LEVELS)}; bad rows: {bad}")
    return labelled


def _agent_results(args: argparse.Namespace) -> tuple[Path, dict[str, Any]]:
    path = args.results or report.latest("*-agent*.json", args.out_dir)
    if path is None or not path.exists():
        raise SystemExit("No agent results found. Run --suite agent first, or pass --results.")
    return path, report.load(path)


def prepare_sheet(args: argparse.Namespace, stamp: str) -> int:
    """A blind labelling sheet: answers only, shuffled, no requested level and no judge label."""
    path, results = _agent_results(args)
    rows = [r for r in results["rows"] if r.get("ok")]
    by_level: dict[str, list[dict[str, Any]]] = {
        lvl: [r for r in rows if r["level"] == lvl] for lvl in LEVELS
    }
    rng = random.Random(f"{path.name}:{args.prepare}")
    for group in by_level.values():
        rng.shuffle(group)
    # Equal numbers per requested level, so agreement is not dominated by one class.
    picked: list[dict[str, Any]] = []
    while len(picked) < args.prepare and any(by_level.values()):
        for lvl in LEVELS:
            if by_level[lvl] and len(picked) < args.prepare:
                picked.append(by_level[lvl].pop())
    rng.shuffle(picked)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    sheet_csv = args.out_dir / f"{stamp}-label-sheet.csv"
    sheet_md = args.out_dir / f"{stamp}-label-sheet.md"
    with sheet_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(HAND_LABEL_COLUMNS)
        for r in picked:
            writer.writerow([r["id"], r["answer_hash"], "", ""])
    from evals.harness.judges import strip_citations_keep_lines
    from papermind.agent.writer import LEVEL_DEFINITIONS

    lines = [
        f"# Blind level labelling ({len(picked)} answers from {path.name})",
        "",
        "For each answer, decide which reader it is written for and put the level in the CSV's human_level column:",
        "",
        *(f"- **{lvl}**: {LEVEL_DEFINITIONS[lvl]}" for lvl in LEVELS),
        "",
        "Judge the register (vocabulary, notation, what is explained vs assumed), not length or correctness.",
        "Do not look up the question or the requested level first.",
        "",
    ]
    for r in picked:
        lines += [
            f"## {r['id']}  (hash {r['answer_hash']})",
            "",
            strip_citations_keep_lines(r["answer"]).strip(),
            "",
            "---",
            "",
        ]
    sheet_md.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {sheet_md} and {sheet_csv}.")
    print(
        f"Fill in human_level in the CSV, then append its rows to {DEFAULT_HAND_LABELS.relative_to(REPO_ROOT)}."
    )
    return 0


async def suite_agreement(args: argparse.Namespace, settings: Settings, stamp: str, started: datetime) -> int:
    if args.prepare:
        return prepare_sheet(args, stamp)
    labels = read_hand_labels(args.labels)
    if not labels:
        print(
            f"{args.labels} has no filled-in labels yet. Run --suite agreement --prepare 30 first.",
            file=sys.stderr,
        )
        return 1
    path, results = _agent_results(args)
    by_id = {r["id"]: r for r in results["rows"]}
    matched: list[tuple[dict[str, str], dict[str, Any]]] = []
    stale: list[str] = []
    unmatched: list[str] = []
    for label in labels:
        row = by_id.get(label["id"])
        if row is None or not row.get("ok"):
            unmatched.append(label["id"])
        elif row.get("answer_hash") != label["answer_text_hash"]:
            # The label judged a different answer than the one in these results.
            stale.append(label["id"])
        else:
            matched.append((label, row))
    judge_labels: list[str | None] = [row.get("calibration_label") for _, row in matched]
    if args.rejudge or any(j is None for j in judge_labels):
        if not settings.has_api_key:
            print("Re-judging needs ANTHROPIC_API_KEY.", file=sys.stderr)
            return 2
        from evals.harness.judges import Judges
        from papermind.llm.client import LLM

        llm = LLM(settings)
        try:
            judges = Judges(llm, settings)
            calls: list[Any] = []
            todo = [i for i, j in enumerate(judge_labels) if args.rejudge or j is None]
            verdicts = await asyncio.gather(
                *(judges.calibration(matched[i][1]["answer"], calls) for i in todo), return_exceptions=True
            )
            for i, v in zip(todo, verdicts, strict=True):
                judge_labels[i] = None if isinstance(v, BaseException) else v.level
        finally:
            await llm.aclose()
    humans = [label["human_level"] for label, _ in matched]
    targets = [row["level"] for _, row in matched]
    summary = {
        "agreement": scorers.judge_agreement(humans, judge_labels),
        # Context for the kappa: how often each rater matched the level that was asked for.
        "human_vs_target": scorers.calibration_accuracy(humans, targets),
        "judge_vs_target": scorers.calibration_accuracy(judge_labels, targets),
        "stale": stale,
        "unmatched": unmatched,
    }
    result = envelope(
        args,
        settings,
        started,
        source_results=str(path),
        summary=summary,
        rows=[
            {
                "id": label["id"],
                "level": row["level"],
                "human_level": label["human_level"],
                "judge_level": j,
                "notes": label.get("notes", ""),
            }
            for (label, row), j in zip(matched, judge_labels, strict=True)
        ],
    )
    finish(args, settings, result, stamp, result["rows"])
    return 0


async def suite_writer(args: argparse.Namespace, settings: Settings, stamp: str, started: datetime) -> int:
    """Calibration of one writer with retrieval held fixed (the gold passage as S1)."""
    from evals.harness.judges import Judges
    from evals.harness.writer_suite import run_writer, summarize_writer, to_dicts, writer_table
    from papermind.agent.writer import ClaudeWriter, MistralWriter
    from papermind.llm.client import LLM

    if not settings.has_api_key:
        print("The writer suite's judges need ANTHROPIC_API_KEY.", file=sys.stderr)
        return 2
    if args.writer == "mistral":
        settings = settings.model_copy(
            update={
                "writer_backend": "mistral",
                **({"mistral_model": args.mistral_model} if args.mistral_model else {}),
            }
        )
    bench = load_rows(args)
    llm = LLM(settings)
    try:
        writer = MistralWriter(settings) if args.writer == "mistral" else ClaudeWriter(llm, settings)
        judges = None if args.no_judges else Judges(llm, settings)
        results = await run_writer(bench.rows, writer, judges, concurrency=args.concurrency)
    finally:
        await llm.aclose()
    summary = summarize_writer(results)
    result = envelope(
        args, settings, started, benchmark=bench.describe(), summary=summary, rows=to_dicts(results)
    )
    variant = "-".join(p for p in (writer.name.replace(":", "_").replace("/", "_"), _variant(args)) if p)
    path = result_path(args.out_dir, "writer", variant, stamp)
    save(result, path)
    print(writer_table(summary))
    print(f"saved {path}")
    return 0


# ---------------------------------------------------------------- main


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s"
    )
    # The MCP servers inherit these settings and share this terminal's stderr.
    settings = get_settings().model_copy(update={"log_level": "INFO" if args.verbose else "WARNING"})
    started = datetime.now(UTC)
    stamp = started.strftime("%Y%m%d-%H%M%S")
    suites = {
        "retrieval": suite_retrieval,
        "agent": suite_agent,
        "latency": suite_agent,
        "agreement": suite_agreement,
        "writer": suite_writer,
    }
    return asyncio.run(suites[args.suite](args, settings, stamp, started))


if __name__ == "__main__":
    raise SystemExit(main())
