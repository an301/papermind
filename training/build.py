"""Build the SFT and DPO datasets from the filtered rows.

SFT (conversational prompt/completion, so the loss is on the answer only):

    {"prompt": [{"role": "user", "content": <mistral prompt>}],
     "completion": [{"role": "assistant", "content": <answer at that level>}]}

DPO (same prompt; same question, same sources, a different level):

    {"prompt": [...], "chosen": [<answer at the asked level>], "rejected": [<answer at another level>]}

The rejected answer is fluent and correct. It is wrong only because it is
pitched at the wrong level, which isolates exactly the behaviour being
trained. Pairs go both ways (beginner-chosen vs expert-rejected and the
reverse) so the model learns the distinction, not "always simpler".
--with-intermediate adds pairs for the middle level, the hardest to hit.

Papers are split, not rows: every row of a held-out paper goes to eval, so
eval loss measures generalisation to new papers, not to new questions about
papers the model trained on.

    .venv/bin/python -m training.build [--eval-papers 3] [--with-intermediate]
"""

from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from typing import Any

from training.common import DATA_DIR, KEPT_PATH, read_jsonl, write_jsonl

OPPOSITE = {"beginner": "expert", "expert": "beginner"}


def sft_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "prompt": row["messages"],
        "completion": [{"role": "assistant", "content": row["answer"]}],
        "level": row["level"],
        "id": row["id"],
    }


def dpo_rows(
    group: dict[str, dict[str, Any]], with_intermediate: bool, rng: random.Random
) -> list[dict[str, Any]]:
    pairs: list[tuple[str, str]] = [(lvl, rej) for lvl, rej in OPPOSITE.items()]
    if with_intermediate:
        pairs.append(("intermediate", rng.choice(["beginner", "expert"])))
    out = []
    for chosen, rejected in pairs:
        if chosen in group and rejected in group:
            out.append(
                {
                    "prompt": group[chosen]["messages"],
                    "chosen": [{"role": "assistant", "content": group[chosen]["answer"]}],
                    "rejected": [{"role": "assistant", "content": group[rejected]["answer"]}],
                    "level": chosen,
                    "id": f"{group[chosen]['group']}:{chosen}>{rejected}",
                }
            )
    return out


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--eval-papers", type=int, default=3, help="papers held out for eval loss (default 3)")
    p.add_argument(
        "--with-intermediate", action="store_true", help="add DPO pairs for the intermediate level"
    )
    p.add_argument("--seed", type=int, default=0)
    args = p.parse_args(argv)
    rng = random.Random(args.seed)

    rows = list(read_jsonl(KEPT_PATH))
    if not rows:
        raise SystemExit(f"No rows in {KEPT_PATH}. Run training.filter first.")
    papers = sorted({r["paper_id"] for r in rows})
    held_out = set(rng.sample(papers, min(args.eval_papers, max(0, len(papers) - 1))))

    groups: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for r in rows:
        groups[r["group"]][r["level"]] = r

    splits: dict[str, dict[str, list[dict[str, Any]]]] = {
        "train": {"sft": [], "dpo": []},
        "eval": {"sft": [], "dpo": []},
    }
    for group in groups.values():
        split = "eval" if next(iter(group.values()))["paper_id"] in held_out else "train"
        splits[split]["sft"] += [sft_row(r) for r in group.values()]
        splits[split]["dpo"] += dpo_rows(group, args.with_intermediate, rng)

    stats: dict[str, Any] = {"held_out_papers": sorted(held_out), "papers": len(papers)}
    for split, sets in splits.items():
        for kind, items in sets.items():
            rng.shuffle(items)
            n = write_jsonl(DATA_DIR / f"{kind}_{split}.jsonl", items)
            stats[f"{kind}_{split}"] = n
            print(f"{kind}_{split}.jsonl  {n:5d} rows")
    (DATA_DIR / "stats.json").write_text(json.dumps(stats, indent=2) + "\n", encoding="utf-8")
    print(f"held out for eval: {', '.join(sorted(held_out))}")


if __name__ == "__main__":
    main()
