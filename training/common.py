"""Shared paths and guards for the writer fine-tuning pipeline.

    generate.py  ->  data/training/raw.jsonl        Claude answers each question at all three levels
    filter.py    ->  data/training/kept.jsonl       drops rows that miss their level or break the rules
    build.py     ->  data/training/{sft,dpo}_{train,eval}.jsonl
    train.py     ->  the LoRA adapter (on a GPU)

Training papers live in their own store (data/training/papers) so they never
show up in the reader's library or in retrieval for the benchmark.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from papermind.config import REPO_ROOT

TRAINING_DIR = REPO_ROOT / "training"
DATA_DIR = REPO_ROOT / "data" / "training"
PAPERS_DIR = DATA_DIR / "papers"
RAW_PATH = DATA_DIR / "raw.jsonl"
KEPT_PATH = DATA_DIR / "kept.jsonl"
FLAGGED_PATH = DATA_DIR / "flagged.jsonl"
REVIEW_PATH = DATA_DIR / "review_sample.md"
BENCHMARK_PAPERS = REPO_ROOT / "evals" / "benchmark" / "papers.txt"
TRAINING_PAPERS = TRAINING_DIR / "papers.txt"

LEVELS = ("beginner", "intermediate", "expert")
_ID = re.compile(r"^\s*([0-9]{4}\.[0-9]{4,5}|[a-z-]+/[0-9]{7})")


def read_ids(path: Path) -> list[str]:
    """arXiv ids from a list file, versions and comments stripped."""
    out: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _ID.match(line)
        if m:
            out.append(m.group(1))
    return out


def training_paper_ids() -> list[str]:
    """The training papers, after checking none is a benchmark paper (train/test contamination)."""
    train = read_ids(TRAINING_PAPERS)
    overlap = sorted(set(train) & set(read_ids(BENCHMARK_PAPERS)))
    if overlap:
        raise SystemExit(
            f"training/papers.txt contains benchmark papers {overlap}. Remove them: "
            "the calibration eval would then partly measure memorisation."
        )
    return train


def read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    if not path.exists():
        return
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            n += 1
    return n
