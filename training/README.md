# Fine-tuning the writer (Mistral 7B, QLoRA SFT + DPO)

The agent decides *what* to say; the writer decides *how to say it at the
reader's level*. Today the writer is Claude with a long rubric. This folder
distils that behaviour into a small open model: Mistral 7B Instruct with a
~55 MB LoRA adapter that hits the requested level reliably without being asked
at length. You cannot fine-tune Claude (closed weights); you can fine-tune
Mistral, and serve it yourself.

```
papers.txt ─ generate.py ─▶ raw.jsonl ─ filter.py ─▶ kept.jsonl ─ build.py ─▶ sft_*.jsonl, dpo_*.jsonl
                                                                                   │
                                     (GPU) train.py --stage sft ─▶ train.py --stage dpo ─▶ adapter on the Hub
                                                                                   │
                     vLLM serves base + adapter ─▶ MistralWriter ─▶ evals --suite writer (base vs tuned)
```

## 1. Data (your laptop, API calls only)

```bash
make train-data-ingest      # the 30 training papers into data/training/papers (free, ~10 min)
make train-data-pilot       # 2 papers x 3 passages: look at the output first (~$0.60)
make train-data MAX_COST=45 # the real run; resumable, stops at the cap (see "Cost")
make train-filter           # rules + a Haiku level check; read data/training/review_sample.md
make train-build            # SFT + DPO files, 3 papers held out for eval loss
```

How each row is made (`generate.py`): for a passage, Claude writes a question
a real reader would ask that the passage answers, plus the plan the agent would
hand the writer. Then the **real ClaudeWriter** answers it three times, at
beginner, intermediate and expert, from the same sources. Each row stores the
exact prompt the fine-tuned writer will see in production
(`papermind/agent/writer.py: mistral_messages`), so training and serving use
the same bytes.

DPO pairs cost nothing extra: for one question, the answer at the asked level
is *chosen* and the answer at the opposite level is *rejected*. Both are
fluent and faithful; the rejected one is wrong only because of its level,
which isolates exactly the behaviour being trained. Pairs go both ways so the
model learns the distinction, not "always simpler".

Train/test contamination: `papers.txt` must not contain any of the 8 benchmark
papers. `common.py` refuses to run if it does, because then the calibration
number would partly measure memorisation.

Filtering (`filter.py`): rules drop rows that would teach the wrong thing (no
citation, invented labels, "I", "Great question", a math-dense "beginner"
answer, odd lengths). `--llm-flag` asks a *different* model (Claude Haiku 4.5)
to label each answer's level blind. Using the eval's judge model here would
make the eval circular. Haiku leans toward "intermediate", so only clear misses
(beginner read as expert or the reverse) are dropped automatically. One-level
disagreements are listed first in `review_sample.md` for you to skim. Delete
any bad rows from `kept.jsonl` by id, then rebuild.

### Cost

The pilot measured about **$0.10 per passage**: one question call plus three
writer calls on Claude Opus 5.5. Three answers per passage:

| passages | SFT rows | DPO pairs (both ways) | cost |
|---|---|---|---|
| 300 (10 per paper) | ~900 | ~600 | ~$30 |
| 450 (15 per paper) | ~1,350 | ~900 | ~$45 |
| 826 (40 per paper, all usable) | ~2,500 | ~1,650 | ~$80 |

Set `--per-paper` and `--max-cost` to taste. A few hundred well-filtered
passages already teach a style change to a 7B model.

## 2. Training (a rented NVIDIA GPU)

This step can't run on a Mac: 4-bit NF4 loading (bitsandbytes) and trl's loss
kernel are CUDA/Linux only. Use Colab Pro or RunPod with an A100.

Open `train_colab.ipynb`, or on any GPU box:

```bash
pip install -r training/requirements-train.txt
python -m training.train --stage sft --out out/sft
python -m training.train --stage dpo --sft-adapter out/sft --out out/dpo --push-to-hub an301/papermind-writer
```

Check the script on your Mac first (tiny model, CPU, 2 steps, about a minute):

```bash
python -m training.train --stage sft --smoke --out /tmp/sft && \
python -m training.train --stage dpo --smoke --sft-adapter /tmp/sft --out /tmp/dpo
```

| | setting | why |
|---|---|---|
| base | `mistralai/Mistral-7B-Instruct-v0.3` | already follows instructions; the data only has to teach level |
| quantisation | 4-bit NF4, double quant, bf16 compute | the frozen 7B fits in ~4 GB; only frozen weights are rounded |
| LoRA | r 16, alpha 32, dropout 0.05, q/k/v/o | ~14 M trainable params (k/v are 4096→1024 under grouped-query attention) |
| SFT | lr 2e-4 cosine, 3% warmup, batch 4×4, 3 epochs, loss on the answer only | QLoRA defaults; best checkpoint by eval loss |
| DPO | lr 5e-6, beta 0.1, batch 2×8, 1 epoch | ~40× lower lr: DPO sharpens, and destabilises easily |
| reference | a frozen copy of the SFT adapter | trl makes it, so no second 7B in memory |

What healthy runs look like in W&B:
- **SFT:** loss falls from ~1.4–1.8 to ~0.9–1.2. If eval loss rises while train loss falls, it's overfitting; the best checkpoint by eval loss is kept.
- **DPO:** loss starts at exactly **0.693** (ln 2). Any other start means the reference is wrong. Verified in the smoke run.
  - `rewards/accuracies` climbs from ~0.5 to ~0.8–0.9.
  - `rewards/margins` grows to ~1–3.
  - If outputs turn short and hedged, the learning rate is too high.

About 2 hours on an A100 (~$3 on RunPod).

## 3. Serving

```bash
pip install vllm
vllm serve mistralai/Mistral-7B-Instruct-v0.3 --enable-lora \
  --lora-modules papermind=an301/papermind-writer --max-lora-rank 16 --port 8001
```

Then in `.env`:

```
WRITER_BACKEND=mistral
MISTRAL_BASE_URL=http://<gpu-box>:8001/v1
MISTRAL_MODEL=papermind
```

The agent loop is unchanged: Claude still picks the context, Mistral writes
the answer. A 7B in 4-bit needs about 5 GB of GPU memory to serve.

## 4. Did it help? (the calibration number)

One vLLM process serves both the base model and the adapter, so the
comparison changes one thing:

```bash
make eval-writer WRITER=mistral MODEL=mistralai/Mistral-7B-Instruct-v0.3   # base
make eval-writer WRITER=mistral MODEL=papermind                            # tuned
make eval-writer WRITER=claude                                             # the teacher, for reference
```

Each benchmark question goes to the writer with its gold passage as the only
source, so retrieval is held fixed and only the writer varies. A blind judge
(Claude Opus, never told the level or which model wrote it) labels each
answer's level. Calibration accuracy is the share that matches the level asked
for, reported overall and per level. Faithfulness is reported next to it,
because a writer can sound simpler by making things up, and a lift that costs
faithfulness is a bad trade.

How to state the result: "calibration went from X% to Y% (Y−X points
absolute) on 174 questions, with faithfulness at Z% vs W%". Here "calibration"
means expertise-level calibration, not confidence calibration. Name the known
weakness yourself: the same model family generated the data and judges it.
The defence is hand-labelling about 30 answers and reporting agreement with
the judge (`make eval-label-sheet`, then `make eval-agreement`).
