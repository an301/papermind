"""Unit tests for the eval harness: scorers, answer-text splitting, judges (fake model), dataset, report.

No network, no API key, no embedding model; part of the default ``pytest`` run.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from pydantic import BaseModel, SecretStr

from evals.harness import judges as J
from evals.harness import report, scorers
from evals.harness.dataset import (
    BenchmarkError,
    BenchmarkRow,
    PaperLibrary,
    answer_text_hash,
    load_benchmark,
    select_rows,
)
from evals.harness.run import parse_args
from evals.harness.wandb_log import WandbLogger, flatten, settings_snapshot
from papermind.agent.writer import LEVEL_DEFINITIONS
from papermind.config import Settings
from papermind.llm.client import CallResult
from papermind.llm.pricing import Usage
from papermind.schemas.paper import Chunk, PaperMeta, PaperRecord

# ---------------------------------------------------------------- retrieval scorers


def test_recall_at_k_is_exact_id_match_within_k() -> None:
    ids = ["p:a", "p:b", "p:c"]
    assert scorers.recall_at_k("p:b", ids, 2) == 1.0
    assert scorers.recall_at_k("p:c", ids, 2) == 0.0
    assert scorers.recall_at_k("p:B", ids, 3) == 0.0
    assert scorers.recall_at_k("p:a", [], 5) == 0.0
    with pytest.raises(ValueError):
        scorers.recall_at_k("p:a", ids, 0)


def test_gold_rank_and_reciprocal_rank() -> None:
    ids = ["x", "y", "z"]
    assert scorers.gold_rank("z", ids) == 3
    assert scorers.gold_rank("q", ids) is None
    assert scorers.reciprocal_rank("y", ids) == 0.5
    assert scorers.reciprocal_rank("z", ids, k=2) == 0.0


def test_mean_ignores_none_and_is_none_when_empty() -> None:
    assert scorers.mean([1.0, None, 0.0]) == 0.5
    assert scorers.mean([None, None]) is None
    assert scorers.ratio(1, 0) is None


# ---------------------------------------------------------------- token F1


def test_token_f1_identical_disjoint_and_partial() -> None:
    assert scorers.token_f1("The model uses eight heads.", "the model uses eight heads") == 1.0
    assert scorers.token_f1("cats purr", "dogs bark") == 0.0
    # pred {a b c d}, ref {a b}: P=0.5, R=1 -> F1=2/3
    assert scorers.token_f1("alpha beta gamma delta", "alpha beta") == pytest.approx(2 / 3)


def test_token_f1_strips_citations_markdown_and_articles() -> None:
    answer = "**Dropout** of 0.1 is applied [S2][S3], see [the paper](http://x)."
    assert scorers.normalize_answer(answer) == "dropout of 01 is applied see paper"
    assert scorers.token_f1(answer, "Dropout of 0.1 is applied, see the paper") == 1.0


def test_math_is_unwrapped_not_deleted() -> None:
    text = r"Scores are divided by $\sqrt{d_k}$ and passed to $$\mathrm{softmax}(QK^\top)$$"
    norm = scorers.normalize_answer(text)
    assert "dk" in norm.split()
    assert "softmax" in norm.split()
    assert "sqrt" not in norm and "mathrm" not in norm
    assert scorers.token_f1(text, "divided by d_k") > 0


def test_token_f1_empty_inputs() -> None:
    assert scorers.token_f1("", "") == 1.0
    assert scorers.token_f1("[S1]", "something") == 0.0


# ---------------------------------------------------------------- latency / cost


@pytest.mark.parametrize("p", [0, 5, 50, 90, 95, 99, 100])
def test_percentile_matches_numpy_linear(p: float) -> None:
    rng = np.random.default_rng(7)
    values = list(rng.exponential(2000, size=37))
    assert scorers.percentile(values, p) == pytest.approx(float(np.percentile(values, p)))


def test_percentile_rejects_bad_input() -> None:
    with pytest.raises(ValueError):
        scorers.percentile([], 50)
    with pytest.raises(ValueError):
        scorers.percentile([1.0], 101)


def test_latency_summary_reports_percentiles_never_the_mean() -> None:
    summary = scorers.latency_summary([100, 200, 300, None, 10_000])
    assert set(summary) == {"n", "p50", "p95", "max"}
    assert summary["n"] == 4
    assert summary["p50"] == 250.0
    assert scorers.latency_summary([]) == {"n": 0, "p50": None, "p95": None, "max": None}


def test_cost_and_cache_summaries() -> None:
    cost = scorers.cost_summary([0.01, 0.03, None])
    assert cost["total_usd"] == 0.04 and cost["mean_usd"] == 0.02 and cost["n"] == 2
    cache = scorers.cache_summary(
        [
            {"input_tokens": 100, "cache_read_input_tokens": 800, "cache_creation_input_tokens": 100},
            {"input_tokens": 0},
        ]
    )
    assert cache["prompt_tokens"] == 1000
    assert cache["cache_hit_rate"] == 0.8


# ---------------------------------------------------------------- calibration + agreement


def test_calibration_accuracy_confusion_and_per_level() -> None:
    labels = ["beginner", "intermediate", None, "expert", "intermediate"]
    targets = ["beginner", "beginner", "expert", "expert", "intermediate"]
    assert scorers.calibration_accuracy(labels, targets) == 0.75
    matrix = scorers.confusion_matrix(labels, targets)
    assert matrix["beginner"] == {"beginner": 1, "intermediate": 1, "expert": 0}
    assert matrix["expert"]["expert"] == 1
    per_level = scorers.per_level_accuracy(labels, targets)
    assert per_level["beginner"] == {"n": 2, "accuracy": 0.5}
    assert per_level["expert"] == {"n": 1, "accuracy": 1.0}
    assert scorers.calibration_accuracy([None], ["beginner"]) is None


def test_cohen_kappa_known_values() -> None:
    assert scorers.cohen_kappa(["a", "b", "a", "b"], ["a", "b", "a", "b"]) == 1.0
    # Classic 2x2 example: po = 0.7, pe = 0.5 -> kappa 0.4.
    a = ["y"] * 25 + ["n"] * 25
    b = ["y"] * 20 + ["n"] * 5 + ["y"] * 10 + ["n"] * 15
    assert scorers.cohen_kappa(a, b) == pytest.approx(0.4)
    # One rater constant: agreement is exactly chance.
    assert scorers.cohen_kappa(["a", "b", "a", "b"], ["a", "a", "a", "a"]) == pytest.approx(0.0)
    # Both constant and identical.
    assert scorers.cohen_kappa(["a", "a"], ["a", "a"]) == 1.0
    assert scorers.cohen_kappa([], []) is None


def test_linear_weighted_kappa_penalises_far_disagreements_more() -> None:
    levels = list(scorers.LEVEL_ORDER)
    human = ["beginner", "intermediate", "expert"] * 4
    near = ["intermediate", "intermediate", "expert"] * 4
    far = ["expert", "intermediate", "expert"] * 4
    k_near = scorers.cohen_kappa(human, near, weights="linear", classes=levels)
    k_far = scorers.cohen_kappa(human, far, weights="linear", classes=levels)
    assert k_near is not None and k_far is not None and k_near > k_far
    # Unweighted kappa cannot tell the two apart.
    assert scorers.cohen_kappa(human, near, classes=levels) == pytest.approx(
        scorers.cohen_kappa(human, far, classes=levels)
    )


def test_judge_agreement_shape() -> None:
    out = scorers.judge_agreement(["beginner", "expert", None], ["beginner", "intermediate", "expert"])
    assert out["n"] == 2
    assert out["agreement"] == 0.5
    assert out["confusion"]["expert"]["intermediate"] == 1  # rows human, columns judge


def test_citation_gates() -> None:
    assert scorers.citation_precision([True, True, False, True]) == 0.75
    assert scorers.citation_precision([]) is None
    # (paper_derived, cited): background paragraphs do not count either way.
    assert scorers.uncited_rate([(True, True), (True, False), (False, False), (True, True)]) == pytest.approx(
        1 / 3
    )
    assert scorers.uncited_rate([(False, False)]) is None


# ---------------------------------------------------------------- answer splitting


ANSWER = """Attention maps a query to a weighted sum of values [S1]. The weights come from a softmax over $q \\cdot k / \\sqrt{d_k}$, e.g. a scaled score. [S2]

## How it is used

$$\\mathrm{Attention}(Q,K,V) = \\mathrm{softmax}(QK^T/\\sqrt{d_k})V$$

- The encoder uses self-attention [S1][S3].
- The decoder masks future positions.

Think of it like a library lookup."""


def test_split_paragraphs_lists_headings_and_display_math() -> None:
    paragraphs = J.split_paragraphs(ANSWER)
    assert len(paragraphs) == 4
    assert paragraphs[0].startswith("Attention maps")
    # The math-only block joined the paragraph before it; the heading is gone.
    assert "$$" in paragraphs[0]
    assert paragraphs[1].startswith("- The encoder")
    assert paragraphs[2].startswith("- The decoder")
    assert paragraphs[3] == "Think of it like a library lookup."


def test_split_sentences_masks_math_and_reattaches_trailing_markers() -> None:
    sentences = J.split_sentences(J.split_paragraphs(ANSWER)[0].split("\n")[0])
    assert len(sentences) == 2
    assert sentences[0].endswith("[S1].")
    # "e.g." did not end the sentence, the math survived intact, and the
    # marker written after the full stop belongs to that sentence.
    assert "$q \\cdot k / \\sqrt{d_k}$" in sentences[1]
    assert sentences[1].endswith("[S2]")
    assert J.split_sentences("A rate of 0.1 is used. Then B follows.") == [
        "A rate of 0.1 is used.",
        "Then B follows.",
    ]


def test_citation_items_one_per_marker_with_siblings() -> None:
    items = J.citation_items(ANSWER)
    assert [(i.label, i.other_labels) for i in items] == [
        ("S1", ()),
        ("S2", ()),
        ("S1", ("S3",)),
        ("S3", ("S1",)),
    ]
    assert "[S" not in items[0].sentence
    assert J.cited_labels("see [S2] and [P1] and [S2, S4]") == ["S2", "S4"]


# ---------------------------------------------------------------- judges with a fake model


class FakeLLM:
    """Answers ``parse`` from a per-schema script and records every request."""

    model = "claude-opus-5-5"

    def __init__(self, script: dict[str, Callable[[str], BaseModel]]) -> None:
        self.script = script
        self.requests: list[dict[str, Any]] = []

    async def parse(
        self, *, system: Any, messages: Any, schema: type[BaseModel], effort: str, max_tokens: int
    ) -> CallResult:
        self.requests.append(
            {
                "system": system[0]["text"],
                "user": messages[0]["content"],
                "schema": schema.__name__,
                "effort": effort,
            }
        )
        parsed = self.script[schema.__name__](messages[0]["content"])
        return CallResult(
            message=None,
            usage=Usage(input_tokens=100, output_tokens=20),
            latency_ms=5,
            ttft_ms=None,
            request_id="req_test",
            cost_usd=0.001,
            model=self.model,
            parsed=parsed,
        )

    async def stream(self, **kw: Any) -> CallResult:
        raise NotImplementedError

    async def prewarm(self, **kw: Any) -> CallResult:
        raise NotImplementedError


def _settings(**kw: Any) -> Settings:
    return Settings(_env_file=None, anthropic_api_key=None, judge_effort="low", **kw)


SOURCES = [
    J.SourceText("S1", "p:sec_1", "Attention maps a query and key-value pairs to an output."),
    J.SourceText("S2", "p:sec_2", "Dot products are scaled by one over the square root of d_k."),
]


async def test_faithfulness_scores_supported_over_total_and_counts_missing_as_unsupported() -> None:
    llm = FakeLLM(
        {
            "_Claims": lambda _: J._Claims(
                claims=[
                    J._Claim(claim="Attention maps queries to outputs.", marked_outside_paper=False),
                    J._Claim(claim="Scores are scaled by 1/sqrt(d_k).", marked_outside_paper=False),
                    J._Claim(claim="Transformers were invented in 2017.", marked_outside_paper=True),
                ]
            ),
            "_ClaimChecks": lambda _: J._ClaimChecks(
                verdicts=[
                    J._ClaimCheck(claim_id=0, reason="stated", supported="yes"),
                    J._ClaimCheck(claim_id=1, reason="stated", supported="yes"),
                ]
            ),
        }
    )
    calls: list[J.JudgeCall] = []
    result = await J.Judges(llm, _settings()).faithfulness("answer [S1]", SOURCES, calls)
    assert (result.supported, result.total, result.missing) == (2, 3, 1)
    assert result.score == pytest.approx(2 / 3)
    assert result.score_unmarked == 1.0
    assert [c.judge for c in calls] == ["faithfulness.claims", "faithfulness.verify"]
    verify = llm.requests[1]
    assert "Is this claim supported by the following context? yes/no" in verify["system"]
    assert "square root of d_k" in verify["user"] and verify["effort"] == "low"


async def test_faithfulness_without_sources_marks_everything_unsupported_without_a_second_call() -> None:
    llm = FakeLLM(
        {"_Claims": lambda _: J._Claims(claims=[J._Claim(claim="X is Y.", marked_outside_paper=False)])}
    )
    calls: list[J.JudgeCall] = []
    result = await J.Judges(llm, _settings()).faithfulness("X is Y.", [], calls)
    assert result.score == 0.0 and len(llm.requests) == 1


async def test_faithfulness_of_an_answer_with_no_claims_is_none() -> None:
    llm = FakeLLM({"_Claims": lambda _: J._Claims(claims=[])})
    result = await J.Judges(llm, _settings()).faithfulness("The paper does not say.", SOURCES, [])
    assert result.score is None


async def test_calibration_judge_is_blind_and_uses_the_writer_definitions() -> None:
    llm = FakeLLM({"_LevelJudgement": lambda _: J._LevelJudgement(evidence="plain words", level="beginner")})
    verdict = await J.Judges(llm, _settings()).calibration("Think of attention like a spotlight [S1].", [])
    assert verdict.level == "beginner"
    request = llm.requests[0]
    for definition in LEVEL_DEFINITIONS.values():
        assert definition in request["system"]
    # Only the explanation, with citation markers removed: no level, no question, no writer name.
    assert request["user"] == "<explanation>\nThink of attention like a spotlight.\n</explanation>"
    assert "beginner" not in request["user"] and "claude" not in request["user"].lower()


async def test_citation_precision_checks_each_marker_against_its_own_passage() -> None:
    def verdicts(user: str) -> J._CitationChecks:
        assert 'passage="S2"' in user and '<passage label="S2"' in user
        return J._CitationChecks(
            verdicts=[
                J._CitationCheck(item_id=0, reason="yes", supported="yes"),
                J._CitationCheck(item_id=1, reason="wrong passage", supported="no"),
            ]
        )

    llm = FakeLLM({"_CitationChecks": verdicts})
    answer = "Attention maps queries to outputs [S1]. Scores are scaled by sqrt(d_k) [S2]. Also see [S9]."
    result = await J.Judges(llm, _settings()).citation_precision(answer, SOURCES, [])
    assert [(i.label, i.supported) for i in result.items] == [("S1", True), ("S2", False), ("S9", False)]
    assert result.items[0].chunk_id == "p:sec_1"
    assert result.precision == pytest.approx(1 / 3)


async def test_uncited_paragraphs_strips_markers_for_the_judge_and_checks_citations_by_regex() -> None:
    seen: list[str] = []

    def verdicts(user: str) -> J._ParagraphChecks:
        seen.append(user)
        return J._ParagraphChecks(
            verdicts=[
                J._ParagraphCheck(paragraph_id=0, reason="method", paper_derived="yes"),
                J._ParagraphCheck(paragraph_id=1, reason="result", paper_derived="yes"),
                J._ParagraphCheck(paragraph_id=2, reason="analogy", paper_derived="no"),
            ]
        )

    llm = FakeLLM({"_ParagraphChecks": verdicts})
    answer = "The paper scales scores [S2].\n\nIt reaches 28.4 BLEU.\n\nLike a spotlight."
    result = await J.Judges(llm, _settings()).uncited_paragraphs(answer, SOURCES, [], paper_title="Attention")
    assert "[S2]" not in seen[0].split("<paragraphs>")[1]
    assert [(p.paper_derived, p.cited) for p in result.paragraphs] == [
        (True, True),
        (True, False),
        (False, False),
    ]
    assert result.rate == 0.5


async def test_judge_answer_records_failures_per_judge() -> None:
    from papermind.llm.client import llm_error

    class Failing(FakeLLM):
        async def parse(self, **kw: Any) -> CallResult:
            if kw["schema"].__name__ == "_LevelJudgement":
                raise llm_error("overloaded", retryable=True)
            return await super().parse(**kw)

    llm = Failing(
        {
            "_Claims": lambda _: J._Claims(claims=[]),
            "_ParagraphChecks": lambda _: J._ParagraphChecks(verdicts=[]),
        }
    )
    out = await J.Judges(llm, _settings()).judge_answer("Plain text.", SOURCES)
    assert out.calibration is None and out.errors and out.errors[0].startswith("calibration")
    assert out.faithfulness is not None and out.citations is not None and out.paragraphs is not None
    assert out.to_dict()["cost_usd"] == pytest.approx(0.002)
    empty = await J.Judges(llm, _settings()).judge_answer("   ", SOURCES)
    assert empty.errors == ["empty answer: nothing to judge"]


# ---------------------------------------------------------------- dataset


def _paper(tmp: Path, paper_id: str, chunk_ids: list[str]) -> None:
    meta = PaperMeta(
        paper_id=paper_id,
        source="arxiv",
        title="T",
        num_pages=1,
        chunker_version="2",
        ingested_at=datetime.now(UTC),
    )
    chunks = [
        Chunk(
            chunk_id=c,
            paper_id=paper_id,
            section_id="s",
            section_title="S",
            text="x" * 500,
            token_count=100,
            page_start=0,
            page_end=0,
        )
        for c in chunk_ids
    ]
    (tmp / paper_id).mkdir(parents=True)
    (tmp / paper_id / "paper.json").write_text(PaperRecord(meta=meta, chunks=chunks).model_dump_json())


def _row(**kw: Any) -> dict[str, Any]:
    base = {
        "id": "1111.0001-g01",
        "paper_id": "1111.0001",
        "question": "What is it?",
        "target_level": "beginner",
        "gold_chunk": "1111.0001:sec_1_intro",
        "reference_answer": "It is a thing.",
        "origin": "generated",
        "query_type": "lookup",
    }
    return {**base, **kw}


def _jsonl(path: Path, rows: list[dict[str, Any]], extra: str = "") -> Path:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n" + extra)
    return path


def test_load_benchmark_validates_labels_against_paper_json(tmp_path: Path) -> None:
    papers = tmp_path / "papers"
    _paper(papers, "1111.0001", ["1111.0001:sec_1_intro", "1111.0001:sec_2_method"])
    library = PaperLibrary(papers)
    good = _jsonl(
        tmp_path / "good.jsonl",
        [_row(), _row(id="1111.0001-n01", origin="natural", gold_chunk="1111.0001:sec_2_method")],
    )
    rows = load_benchmark(good, library=library)
    assert [r.id for r in rows] == ["1111.0001-g01", "1111.0001-n01"]
    assert [r.id for r in load_benchmark(good, origin="natural", library=library)] == ["1111.0001-n01"]
    assert load_benchmark(good, papers=["2222.0002"], library=library) == []

    bad = _jsonl(
        tmp_path / "bad.jsonl",
        [
            _row(),
            _row(),
            _row(id="x-1", gold_chunk="1111.0001:sec_9_gone"),
            _row(id="x-2", paper_id="3333.0003"),
        ],
        extra='{"id": "x-3", "paper_id": "1111.0001"}\n',
    )
    with pytest.raises(BenchmarkError) as err:
        load_benchmark(bad, library=library)
    text = "\n".join(err.value.problems)
    assert "appears 2 times" in text
    assert "sec_9_gone" in text and "does not exist" in text
    assert "3333.0003 is not ingested" in text
    assert "line 5" in text
    lenient = load_benchmark(bad, library=library, strict=False)
    assert [r.id for r in lenient] == []  # duplicates and bad labels all dropped


def test_benchmark_row_rejects_unknown_fields_and_values() -> None:
    with pytest.raises(ValueError):
        BenchmarkRow.model_validate(_row(gold_chunks="typo"))
    with pytest.raises(ValueError):
        BenchmarkRow.model_validate(_row(target_level="advanced"))
    with pytest.raises(ValueError):
        BenchmarkRow.model_validate(_row(query_type="trivia"))


def test_select_rows_is_balanced_and_deterministic() -> None:
    rows = [
        BenchmarkRow.model_validate(
            _row(id=f"{p}-{lvl}-{i}", paper_id=p, target_level=lvl, gold_chunk=f"{p}:c")
        )
        for p in ("1111.0001", "2222.0002")
        for lvl in ("beginner", "intermediate", "expert")
        for i in range(5)
    ]
    picked = select_rows(rows, 6)
    assert len(picked) == 6
    assert {(r.paper_id, r.target_level) for r in picked} == {
        (p, lvl) for p in ("1111.0001", "2222.0002") for lvl in ("beginner", "intermediate", "expert")
    }
    assert select_rows(rows, 6) == picked
    assert select_rows(rows, None) == rows


def test_answer_text_hash_ignores_whitespace_only() -> None:
    assert answer_text_hash("a  b\nc") == answer_text_hash("a b c")
    assert answer_text_hash("a b c") != answer_text_hash("a b d")


# ---------------------------------------------------------------- report


def _agent_row(level: str, **kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": f"r-{level}",
        "level": level,
        "origin": "generated",
        "ok": True,
        "recall_writer": 1.0,
        "recall_first5": 1.0,
        "recall_union": 1.0,
        "faithfulness": 1.0,
        "claims_supported": 4,
        "claims_total": 4,
        "f1": 0.3,
        "calibration_label": level,
        "citations_supported": 3,
        "citations_total": 3,
        "paragraphs_paper_derived": 2,
        "paragraphs_uncited": 0,
        "latency_ms": 20_000,
        "ttft_ms": 9_000,
        "answer_ms": 19_000,
        "usage": {
            "cost_usd": 0.05,
            "input_tokens": 100,
            "cache_read_input_tokens": 900,
            "model_calls": 4,
            "tool_calls": 2,
        },
        "judge_cost_usd": 0.01,
        "judgement": {},
        "judge_errors": [],
        "error_code": None,
    }
    return {**base, **kw}


def test_summarize_agent_slices_by_level_and_applies_gates() -> None:
    rows = [
        _agent_row("beginner", calibration_label="intermediate", citations_supported=1, citations_total=2),
        _agent_row("intermediate"),
        _agent_row("expert", paragraphs_uncited=1),
        _agent_row("expert", id="r-err", ok=False, error_code="overloaded", usage={"cost_usd": 0.02}),
    ]
    s = report.summarize_agent(rows)
    assert s["n"] == 4 and s["answered"] == 3
    assert s["by_level"]["beginner"]["calibration"] == 0.0
    assert s["by_level"]["expert"]["n"] == 1
    assert s["calibration"]["confusion"]["beginner"]["intermediate"] == 1
    assert s["gates"]["citation_precision"]["value"] == pytest.approx(7 / 8)
    assert s["gates"]["citation_precision"]["pass"] is False
    assert s["gates"]["uncited_rate"]["value"] == pytest.approx(1 / 6)
    assert s["cost"]["total_usd"] == pytest.approx(0.17)  # the failed turn was billed too
    assert s["latency"]["turn_ms"]["n"] == 3  # ...but is not a fast answer
    assert s["errors"]["by_code"] == {"overloaded": 1}
    text = report.agent_tables(s)
    assert "beginner" in text and "Design gates" in text and "FAIL" in text


def test_latency_ab_reports_relative_reduction() -> None:
    def run(config: str, ms: int, cost: float) -> dict[str, Any]:
        rows = [
            _agent_row("beginner", latency_ms=ms, ttft_ms=ms // 2, answer_ms=ms, usage={"cost_usd": cost})
        ]
        return {"suite": "latency", "config": config, "summary": report.summarize_agent(rows), "rows": rows}

    table = report.latency_ab_table(run("baseline", 20_000, 0.10), run("optimized", 15_000, 0.06))
    assert "+25.0%" in table and "+40.0%" in table
    assert "WARNING" not in table


def test_retrieval_tables_render() -> None:
    cell = {"n": 2, "recall@5": 0.5, "recall@10": 1.0, "mrr@10": 0.4}
    summary = {
        "n": 2,
        "modes": ["bm25", "hybrid"],
        "ks": [5, 10],
        "overall": {"bm25": cell, "hybrid": cell},
        "by_origin": {"generated": {"bm25": cell, "hybrid": cell}},
        "headline": {"metric": "hybrid recall@5", "value": 0.5},
    }
    text = report.retrieval_tables(summary)
    assert (
        "bm25" in text and "50.0%" in text and "0.400" in text and "Headline: hybrid recall@5 = 50.0%" in text
    )


# ---------------------------------------------------------------- CLI + W&B


def test_cli_latency_suite_requires_a_config() -> None:
    with pytest.raises(SystemExit):
        parse_args(["--suite", "latency"])
    args = parse_args(
        ["--suite", "retrieval", "--modes", "bm25,hybrid", "--k", "5,10", "--papers", "a,b", "c"]
    )
    assert args.modes == ["bm25", "hybrid"] and args.k == [5, 10] and args.papers == ["a", "b", "c"]


def test_wandb_snapshot_never_contains_secrets_and_disabled_logger_is_a_no_op() -> None:
    settings = _settings(wandb_mode="disabled").model_copy(
        update={"anthropic_api_key": SecretStr("sk-secret")}
    )
    snap = settings_snapshot(settings)
    assert "anthropic_api_key" not in snap and "neo4j_password" not in snap
    assert "sk-secret" not in json.dumps(snap, default=str)
    assert snap["parallel_tools"] is True and snap["prompt_caching"] is True
    with WandbLogger("retrieval", settings=settings, config=snap) as logger:
        assert not logger.enabled
        logger.log_summary({"a": 1})
        logger.log_table("rows", [{"a": 1}])
    assert flatten({"overall": {"hybrid": {"recall@5": 0.9, "n": 3}}, "modes": ["x"], "ok": True}) == {
        "overall/hybrid/recall@5": 0.9,
        "overall/hybrid/n": 3,
        "ok": 1,
    }
