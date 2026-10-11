"""The fine-tuning data steps: contamination guard, filter rules, SFT/DPO shapes. No torch, no API."""

from __future__ import annotations

import random

import pytest

from training import build, common
from training import filter as filter_rules


def row(level: str, answer: str, group: str = "p:sec_1", **extra) -> dict:
    return {
        "id": f"{group}:{level}",
        "group": group,
        "paper_id": group.split(":")[0],
        "level": level,
        "answer": answer,
        "source_labels": ["S1", "S2"],
        "messages": [{"role": "user", "content": f"prompt for {level}"}],
        "question": "why?",
        **extra,
    }


GOOD = " ".join(["word"] * 120) + " [S1]."


def test_benchmark_papers_can_never_be_training_papers(tmp_path, monkeypatch) -> None:
    bench = tmp_path / "bench.txt"
    train = tmp_path / "train.txt"
    bench.write_text("1706.03762v7\n")
    train.write_text("1409.0473   # fine\n1706.03762  # contamination\n")
    monkeypatch.setattr(common, "BENCHMARK_PAPERS", bench)
    monkeypatch.setattr(common, "TRAINING_PAPERS", train)
    with pytest.raises(SystemExit, match=r"1706\.03762"):
        common.training_paper_ids()


def test_the_real_training_list_is_clean() -> None:
    ids = common.training_paper_ids()
    assert len(ids) == 30 and len(set(ids)) == 30


def test_a_clean_row_passes_the_rules() -> None:
    assert filter_rules.rule_flags(row("beginner", GOOD)) == []


@pytest.mark.parametrize(
    ("answer", "flag"),
    [
        (" ".join(["word"] * 120), "no citation"),
        (GOOD + " [S7]", "unknown labels"),
        ("I think " + GOOD, "first person"),
        ("Great question. " + GOOD, "filler"),
        ("Short [S1].", "words"),
    ],
)
def test_rules_catch_what_would_teach_the_wrong_thing(answer: str, flag: str) -> None:
    assert any(flag in f for f in filter_rules.rule_flags(row("intermediate", answer)))


def test_a_beginner_answer_dense_with_math_is_flagged_but_an_expert_one_is_not() -> None:
    mathy = GOOD + " " + " ".join(f"$x_{i}$" for i in range(6))
    assert any("math" in f for f in filter_rules.rule_flags(row("beginner", mathy)))
    assert not any("math" in f for f in filter_rules.rule_flags(row("expert", mathy)))


def test_sft_rows_put_the_loss_on_the_answer_only() -> None:
    out = build.sft_row(row("expert", GOOD))
    assert out["prompt"] == [{"role": "user", "content": "prompt for expert"}]
    assert out["completion"] == [{"role": "assistant", "content": GOOD}]


def test_dpo_pairs_go_both_ways_and_reject_only_the_wrong_level() -> None:
    group = {lvl: row(lvl, f"{lvl} answer [S1]") for lvl in common.LEVELS}
    pairs = build.dpo_rows(group, with_intermediate=False, rng=random.Random(0))
    assert {p["id"].split(":")[-1] for p in pairs} == {"beginner>expert", "expert>beginner"}
    for p in pairs:
        chosen, rejected = p["id"].split(":")[-1].split(">")
        assert p["prompt"] == group[chosen]["messages"]  # the prompt asks for the chosen level
        assert p["chosen"][0]["content"].startswith(chosen)
        assert p["rejected"][0]["content"].startswith(rejected)


def test_intermediate_pairs_are_optional_and_need_both_answers() -> None:
    group = {lvl: row(lvl, f"{lvl} [S1]") for lvl in ("beginner", "intermediate")}
    assert build.dpo_rows(group, with_intermediate=True, rng=random.Random(1)) in (
        [],  # expert missing: no beginner<->expert pair; intermediate drew expert as its rival
        [
            {
                "prompt": group["intermediate"]["messages"],
                "chosen": [{"role": "assistant", "content": "intermediate [S1]"}],
                "rejected": [{"role": "assistant", "content": "beginner [S1]"}],
                "level": "intermediate",
                "id": "p:sec_1:intermediate>beginner",
            }
        ],
    )
