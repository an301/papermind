"""Agent suite under pytest: every selected row answered by the real agent, judged, logged. Spends money.

    .venv/bin/pytest -m eval evals/test_agent.py
    .venv/bin/pytest -m eval evals/test_agent.py -k "1706.03762"      # a subset

The selected rows are answered together in a session fixture (concurrency 4)
rather than one per test, so a full run takes minutes, not an hour; each
row's test then asserts on its own result. The run is saved to evals/results/
and logged to W&B like a CLI run.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest

from evals.harness import report
from evals.harness.dataset import RESULTS_DIR, BenchmarkRow
from evals.harness.runner import DEFAULT_CONCURRENCY, AgentHarness, AgentRowResult
from evals.harness.wandb_log import WandbLogger, settings_snapshot
from papermind.config import get_settings

pytestmark = pytest.mark.eval

# Per-row regression floor on faithfulness (supported claims / claims).
FAITHFULNESS_FLOOR = 0.7


def _selected_rows(session: pytest.Session) -> list[BenchmarkRow]:
    """The rows of this module's tests that survived -k/-m selection, in order."""
    seen: dict[str, BenchmarkRow] = {}
    for item in session.items:
        if not isinstance(item, pytest.Function) or item.module.__name__ != __name__:
            continue
        callspec = getattr(item, "callspec", None)
        if callspec is not None and "row" in callspec.params:
            row = callspec.params["row"]
            seen.setdefault(row.id, row)
    return list(seen.values())


@pytest.fixture(scope="session")
def agent_results(
    request: pytest.FixtureRequest, eval_report: Callable[[str, str], None]
) -> dict[str, AgentRowResult]:
    settings = get_settings().model_copy(update={"log_level": "WARNING"})
    if not settings.has_api_key:
        pytest.skip("ANTHROPIC_API_KEY is not set; the agent suite calls the model")
    rows = _selected_rows(request.session)
    if not rows:
        pytest.skip("no benchmark rows selected")

    async def run() -> list[AgentRowResult]:
        async with AgentHarness(settings, concurrency=DEFAULT_CONCURRENCY, judge=True) as harness:
            return await harness.run_rows(rows)

    started = datetime.now(UTC)
    results = asyncio.run(run())
    dicts = [r.to_dict() for r in results]
    summary = report.summarize_agent(dicts)
    stamp = started.strftime("%Y%m%d-%H%M%S")
    path = RESULTS_DIR / f"{stamp}-agent-pytest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {
        "suite": "agent",
        "started_at": started.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "runner": "pytest",
        "settings": settings_snapshot(settings),
        "summary": summary,
        "rows": dicts,
    }
    path.write_text(json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8")
    with WandbLogger(
        "agent", settings=settings, config={"runner": "pytest", **result["settings"]}, name=path.stem
    ) as wb:
        wb.log_summary(summary)
        wb.log_table("rows", dicts)
    eval_report("agent suite", f"{report.agent_tables(summary)}\nSaved {path}")
    return {r.id: r for r in results}


def test_row_faithfulness(
    row: BenchmarkRow, agent_results: dict[str, AgentRowResult], record_property: Callable
) -> None:
    r = agent_results[row.id]
    for key in (
        "recall_writer",
        "faithfulness",
        "f1",
        "calibration_label",
        "citation_precision",
        "latency_ms",
    ):
        record_property(key, getattr(r, key))
    assert r.ok, f"turn failed: {r.error_code}: {r.error}"
    assert not r.judge_errors, f"judge failed: {r.judge_errors}"
    if r.faithfulness is None:
        # No factual claims (e.g. "the paper does not say"): nothing to be
        # unfaithful about. Recall and F1 catch an answer that dodged.
        return
    unsupported = [
        c["claim"]
        for c in (r.judgement or {}).get("faithfulness", {}).get("claims", [])
        if not c["supported"]
    ]
    assert r.faithfulness >= FAITHFULNESS_FLOOR, (
        f"faithfulness {r.faithfulness:.2f} < {FAITHFULNESS_FLOOR}; unsupported claims: {unsupported}"
    )


def test_citation_precision_gate(agent_results: dict[str, AgentRowResult]) -> None:
    summary = report.summarize_agent([r.to_dict() for r in agent_results.values()])
    gate = summary["gates"]["citation_precision"]
    if gate["value"] is None:
        pytest.skip("no citations in the selected answers")
    assert gate["pass"], (
        f"citation precision {gate['value']:.3f} < {gate['threshold']} (by level: {gate['by_level']})"
    )


def test_uncited_paragraph_gate(agent_results: dict[str, AgentRowResult]) -> None:
    summary = report.summarize_agent([r.to_dict() for r in agent_results.values()])
    gate = summary["gates"]["uncited_rate"]
    if gate["value"] is None:
        pytest.skip("no paper-derived paragraphs in the selected answers")
    assert gate["pass"], (
        f"uncited paper-derived paragraphs {gate['value']:.3f} >= {gate['threshold']} (by level: {gate['by_level']})"
    )
