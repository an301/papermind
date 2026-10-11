# PaperMind benchmark

The benchmark is the data. The harness in `evals/harness/` is the code that loops
over it, runs the system, scores the results and logs them. You can swap the
questions without changing the harness.

| file | what |
|---|---|
| `papers.txt` | the 8 benchmark papers, each pinned to an arXiv version |
| `questions.jsonl` | 150 `generated` rows and 24 `natural` rows, one JSON object per line |
| `hand_labels.csv` | human level labels for judge agreement (header-only until someone labels) |

Each row:

```json
{"id": "1706.03762-g07", "paper_id": "1706.03762", "question": "...",
 "target_level": "beginner|intermediate|expert", "gold_chunk": "1706.03762:sec_3.2.1_scaled_dot_product_attention",
 "reference_answer": "...", "origin": "generated|natural",
 "query_type": "lookup|conceptual|numeric|method|comparison|motivation"}
```

`gold_chunk` is a chunk id from `data/papers/<paper_id>/paper.json`. Chunk ids
come from section numbers and titles, so they stay the same when a paper is
re-ingested. They can still move if the arXiv version or the chunker changes.
That is why `papers.txt` pins versions. The loader checks every label against
`paper.json` and lists every row that fails:

```bash
.venv/bin/python -m evals.harness.dataset          # validate + counts by paper, level, origin, query type
```

## How the 150 generated questions were made

They were written backwards from the chunks:

1. **Pick the chunk first.** Chunks from References, Acknowledgements and
   front matter were skipped, and so were chunks under about 400 characters.
   The picks were spread across each paper's sections (introduction, method
   subsections, experiments, ablations, conclusion) and across query types.
2. **Write the question.** A strong model (Claude Opus 5.5) wrote a question
   that this one chunk answers, at a target level (about a third each of
   beginner, intermediate and expert, phrased the way that reader would ask),
   plus a 2-4 sentence reference answer that uses only the chunk. The label
   costs nothing because we already know which chunk the question came from. A
   question that two chunks answer equally well was sharpened or dropped.
3. **Check it.** A separate pass audited every row against `paper.json`. It
   asked: is the gold chunk really the one that answers the question? Does
   another chunk answer it just as well? Is the reference fully supported by
   the chunk, with no outside facts and no wrong numbers? Does the phrasing
   match the target level? Fixes were made in place and ids were kept.
4. **Merge.** The rows were merged into `questions.jsonl`, trimmed to exactly
   150 generated rows while keeping the level and query-type balance.

`scripts/generate_benchmark.py` does steps 1-2 through the API, to regenerate
or extend the benchmark. Its output goes to `evals/results/`, not into this
directory. Candidates must pass step 3 before they are merged here.

```bash
.venv/bin/python scripts/generate_benchmark.py --papers 1706.03762 --per-paper 5 --dry-run   # prints the request, no API call
```

## The natural-phrasing questions, and why they exist

A generated question borrows the chunk's vocabulary. The model writing it has
just read "label smoothing of value ε_ls = 0.1", so it asks about "label
smoothing". That flatters BM25, which only matches words, and makes every
recall number look better than real use. The 24 `natural` rows (3 per paper)
are phrased the way people actually type, and they avoid the chunk's
distinctive terms on purpose: "how long did this thing take to train lol",
"is it actually any better than plain sgd for convnets or nah". Each still has
a verified gold chunk.

Always report the two origins separately (`--origin generated|natural`, and
the "by origin" table). The gap between them is the result, not noise. When
this README was written, hybrid recall@5 was about 93% on generated rows and
about 67% on natural rows. Rerun the retrieval suite to get current numbers.

## Running it

```bash
.venv/bin/python -m evals.harness.run --suite retrieval --modes bm25,dense,hybrid --k 5,10   # no API key, ~10s
.venv/bin/pytest evals                                   # scorer unit tests + retrieval suite with its recall floor
.venv/bin/python -m evals.harness.run --suite agent --limit 24                              # spends money
.venv/bin/pytest -m eval evals/test_agent.py                                                # spends money
.venv/bin/python -m evals.harness.run --suite latency --config baseline --limit 24          # spends money
.venv/bin/python -m evals.harness.run --suite latency --config optimized --limit 24         # prints the A/B
.venv/bin/python -m evals.harness.report evals/results/<file>.json                          # re-render a run
```

Results are written to `evals/results/` (gitignored). W&B runs are offline by
default (`WANDB_MODE`) and are written to `wandb/`.

How each number is measured:

| metric | how | judge? |
|---|---|---|
| recall@k | exact chunk-id match against `gold_chunk` | no |
| agent recall | the headline counts the gold chunk among the passages handed to the writer; the first search's top 5 and the union of all searches are also reported | no |
| token F1 | SQuAD normalisation vs `reference_answer`, after removing citations, markdown and math markup | no |
| faithfulness | the answer is split into atomic claims, and each claim is checked against only the passages the writer saw. A claim that is true in general but not in those passages counts as unsupported | yes |
| calibration | a blind judge sees only the answer and labels its level, which is compared with `target_level`. Always sliced by level | yes |
| citation precision | for each `[S#]`, is the sentence supported by that specific passage? Gate: >= 0.95 (DESIGN.md A13) | yes |
| uncited paragraphs | of the paragraphs that state the paper's content, the share with no `[S#]`. Gate: < 0.05 (A13) | yes |
| latency | p50/p95 of the turn, the first answer token, and answer done. Never the mean | no |
| cost | per query, from API usage, with cache reads and writes | no |

Two things about the agent runs. First, each level is answered for a fixed
synthetic reader whose known concepts match the level definition
(`EVAL_READERS` in `evals/harness/runner.py`). Without that, a reader who
knows nothing would get intuition-level answers at every level. Second, the
mastery pass is off so those readers do not change mid-run. As a result,
reported cost and turn latency do not include the post-answer classifier.

## Hand-labelling 30 answers (judge agreement)

The calibration judge is the same model family that wrote the questions and
the answers. Judges also tend to favour long, confident text. A calibration
number is only as trustworthy as the judge's agreement with a person, so
measure that agreement:

1. Run the agent suite (`--suite agent`, a `--limit 60` run is enough).
2. Make a blind sheet: `.venv/bin/python -m evals.harness.run --suite agreement --prepare 30`.
   This writes `evals/results/<ts>-label-sheet.md`, which holds the answers
   only, shuffled and balanced across requested levels, and
   `<ts>-label-sheet.csv`, which has `id` and `answer_text_hash` filled in.
3. Read each answer in the `.md` and write `beginner`, `intermediate` or
   `expert` in `human_level`. The sheet repeats the definitions the writer and
   the judge use. Judge the register (vocabulary, notation, what is explained
   versus assumed), not length or correctness. Do not look at the question or
   the requested level first. Note borderline calls in `notes`. Ideally the
   labeller is not the person who tuned the writer prompt.
4. Append the filled rows to `hand_labels.csv` and commit it.
5. `.venv/bin/python -m evals.harness.run --suite agreement --labels evals/benchmark/hand_labels.csv`
   reports raw agreement, Cohen's kappa, linear-weighted kappa (levels are
   ordinal, so beginner vs expert is a bigger miss than beginner vs
   intermediate) and the confusion matrix. Add `--rejudge` to re-run the judge
   on the same texts.

`answer_text_hash` ties a label to the exact text it judged. If the agent is
re-run, it produces new answers, and labels whose hash no longer matches are
reported as stale and skipped, never carried over. As a rough guide, a kappa
below about 0.4 means the calibration numbers should not be quoted, and 0.6 or
above is substantial agreement.

## Train/test contamination

These 8 papers, their chunks, these questions and these reference answers are
**test data**. Never use them as fine-tuning data for the writer (the planned
Mistral adapter) or for any other model. That includes using them as seed
examples for synthetic data, as few-shot examples in a prompt, or as
"held-out" validation that is used for model selection. A writer trained on
explanations of these papers would be measured on memory rather than skill,
and every calibration and faithfulness number would go up for the wrong
reason. Training data must come from other papers. Check the paper ids and
arXiv ids against `papers.txt` before every training run. If this set is ever
used for training, replace it rather than reusing it.
