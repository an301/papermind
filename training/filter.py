"""Filter the generated answers before training on them.

One bad row teaches the wrong thing as hard as a good row teaches the right
one: a "beginner" answer full of notation trains the model to call notation
beginner-friendly. Two passes:

1. Rules, on every row: a citation exists and only to real labels; no "I",
   no "Great question", no labelling the reader; a beginner answer isn't
   dense with math; lengths are sane.
2. Optional --llm-flag: a cheap, *different* model (Claude Haiku 4.5) labels
   each answer's level blind. Two levels off (beginner read as expert) is
   dropped; one level off goes to the top of review_sample.md for a person,
   because the cheap classifier leans toward "intermediate" (measured on the
   pilot: 7 of 18 rows, all called intermediate, including plainly beginner prose).
   It must not be the model that judges calibration in the eval (Opus):
   filtering with the eval's own judge trains toward that judge's taste and
   makes the calibration number circular.

Flagged rows go to flagged.jsonl for a human look; the rest to kept.jsonl.
review_sample.md holds a random sample of kept rows to spot-check by hand.

    .venv/bin/python -m training.filter                 # rules only, free
    .venv/bin/python -m training.filter --llm-flag      # + the Haiku level check (cents per 100 rows)
"""

from __future__ import annotations

import argparse
import asyncio
import random
import re
from typing import Any, Literal

from anthropic import AsyncAnthropic
from pydantic import BaseModel

from papermind.agent.writer import LEVEL_DEFINITIONS
from papermind.config import get_settings
from training.common import FLAGGED_PATH, KEPT_PATH, RAW_PATH, REVIEW_PATH, read_jsonl, write_jsonl

FLAG_MODEL = "claude-haiku-4-5"

FIRST_PERSON = re.compile(r"(^|[.!?]\s+)I\b|\bI(’|')(m|ll|ve|d)\b")
FILLER = re.compile(r"great question|as a beginner|as an expert|as you may know", re.I)
MATH = re.compile(r"\$\$[\s\S]+?\$\$|\$[^$\n]+\$")
LABEL = re.compile(r"\[([SP]\d+)\]")

WORDS = {"beginner": (60, 900), "intermediate": (50, 900), "expert": (30, 800)}


def rule_flags(row: dict[str, Any]) -> list[str]:
    answer: str = row["answer"]
    flags: list[str] = []
    if not answer.strip():
        return ["empty"]
    labels = set(LABEL.findall(answer))
    if not labels:
        flags.append("no citation")
    unknown = {lab for lab in labels if lab.startswith("S")} - set(row["source_labels"])
    if unknown:
        flags.append(f"unknown labels {sorted(unknown)}")
    if FIRST_PERSON.search(answer):
        flags.append("first person")
    if FILLER.search(answer):
        flags.append("filler or labels the reader")
    words = len(answer.split())
    low, high = WORDS[row["level"]]
    if not low <= words <= high:
        flags.append(f"{words} words")
    if row["level"] == "beginner" and len(MATH.findall(answer)) > 4:
        flags.append("beginner answer dense with math")
    return flags


class LevelGuess(BaseModel):
    level: Literal["beginner", "intermediate", "expert"]


FLAG_SYSTEM = (
    "Classify the expertise level an explanation is written for.\n"
    + "\n".join(f"{level}: {text}" for level, text in LEVEL_DEFINITIONS.items())
    + "\nReply with the level only."
)


async def llm_flags(rows: list[dict[str, Any]], concurrency: int) -> dict[str, str]:
    """Row id -> the level Haiku assigned, for rows where it disagrees with the target."""
    key = get_settings().anthropic_api_key
    client = AsyncAnthropic(api_key=key.get_secret_value() if key else None)
    gate = asyncio.Semaphore(concurrency)
    out: dict[str, str] = {}

    async def one(row: dict[str, Any]) -> None:
        async with gate:
            text = LABEL.sub("", row["answer"])
            resp = await client.messages.parse(
                model=FLAG_MODEL,
                max_tokens=64,
                system=FLAG_SYSTEM,
                messages=[{"role": "user", "content": f"<explanation>\n{text}\n</explanation>"}],
                output_format=LevelGuess,
            )
            guess = resp.parsed_output
            if guess is not None and guess.level != row["level"]:
                out[row["id"]] = guess.level

    await asyncio.gather(*(one(r) for r in rows))
    await client.close()
    return out


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--llm-flag", action="store_true", help=f"also flag level misses with {FLAG_MODEL}")
    p.add_argument("--concurrency", type=int, default=8)
    p.add_argument("--review", type=int, default=40, help="kept rows to sample for hand review (default 40)")
    args = p.parse_args(argv)

    rows = list(read_jsonl(RAW_PATH))
    if not rows:
        raise SystemExit(f"No rows in {RAW_PATH}. Run training.generate first.")
    misses = asyncio.run(llm_flags(rows, args.concurrency)) if args.llm_flag else {}

    kept, flagged = [], []
    rank = {"beginner": 0, "intermediate": 1, "expert": 2}
    borderline: list[dict] = []
    for row in rows:
        reasons = rule_flags(row)
        guess = misses.get(row["id"])
        if guess and abs(rank[guess] - rank[row["level"]]) == 2:
            # beginner <-> expert: unambiguous, drop it.
            reasons.append(f"reads as {guess}")
        elif guess and not reasons:
            # One level off: the cheap classifier leans toward the middle level, so these are
            # judgment calls for a person (review_sample.md), not automatic drops.
            borderline.append({**row, "flags": [f"reads as {guess}"]})
        (flagged if reasons else kept).append({**row, "flags": reasons} if reasons else row)

    write_jsonl(KEPT_PATH, kept)
    write_jsonl(FLAGGED_PATH, flagged)
    rest = [r for r in kept if r["id"] not in {b["id"] for b in borderline}]
    sample = borderline + random.Random(0).sample(rest, min(max(0, args.review - len(borderline)), len(rest)))
    REVIEW_PATH.write_text(
        "# Hand review sample\n\nRead each answer against its level. Delete bad rows from kept.jsonl by id.\n\n"
        + "\n\n".join(
            f"## {r['id']} ({r['level']}){'  FLAG: ' + ', '.join(r['flags']) if r.get('flags') else ''}"
            f"\n\n**Q:** {r['question']}\n\n{r['answer']}"
            for r in sample
        ),
        encoding="utf-8",
    )
    by_level = {lvl: sum(r["level"] == lvl for r in kept) for lvl in ("beginner", "intermediate", "expert")}
    print(f"kept {len(kept)} / {len(rows)}  {by_level}")
    print(f"flagged {len(flagged)} -> {FLAGGED_PATH.relative_to(FLAGGED_PATH.parents[2])}")
    reasons: dict[str, int] = {}
    for r in flagged:
        for f in r["flags"]:
            key = re.sub(r"\d+", "N", f)
            reasons[key] = reasons.get(key, 0) + 1
    for reason, n in sorted(reasons.items(), key=lambda kv: -kv[1]):
        print(f"  {n:5d}  {reason}")
    print(f"review sample: {REVIEW_PATH} ({len(borderline)} one-level disagreements listed first)")


if __name__ == "__main__":
    main()
