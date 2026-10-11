"""QLoRA fine-tuning of the writer: SFT, then DPO. Runs on one NVIDIA GPU (an A100 is ~2 h total).

    pip install -r training/requirements-train.txt
    python -m training.train --stage sft --out out/sft
    python -m training.train --stage dpo --sft-adapter out/sft --out out/dpo --push-to-hub an301/papermind-writer

--smoke runs both stages end to end on CPU with a tiny model and a handful of
rows (no 4-bit, no GPU), to check the script before renting a GPU.

What each setting is for (numbers from the QLoRA and DPO papers' defaults):
- 4-bit NF4 with double quantisation: the frozen 7B base fits in ~4 GB. Only
  frozen weights are rounded; the trainable LoRA matrices stay full precision.
- LoRA r=16, alpha=32 on q/k/v/o: ~14 M trainable parameters (Mistral's
  grouped-query attention makes k and v 4096 -> 1024). A style change is low-rank.
- SFT lr 2e-4, cosine, 3% warmup, effective batch 16, loss on the answer only.
- DPO lr 5e-6 (~40x lower: it sharpens an existing behaviour and destabilises
  easily), beta 0.1, 1 epoch. The reference is the SFT adapter itself: trl
  copies it into a frozen "ref" adapter, so no second 7B model is loaded.
  At step 0 the loss is ln 2 = 0.693; anything else means the reference is wrong.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import torch
from datasets import load_dataset
from peft import LoraConfig, PeftModel, prepare_model_for_kbit_training
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from trl import DPOConfig, DPOTrainer, SFTConfig, SFTTrainer

BASE = "mistralai/Mistral-7B-Instruct-v0.3"
SMOKE_BASE = "HuggingFaceTB/SmolLM2-135M-Instruct"
DATA = Path(__file__).resolve().parent.parent / "data" / "training"
TARGETS = ["q_proj", "k_proj", "v_proj", "o_proj"]


class _EagerLogProbs:
    """Pure-PyTorch stand-in for trl's chunked log-prob kernel, for --smoke on macOS only.

    trl's fused LM-head loss calls a Triton kernel, and Triton ships with PyTorch
    on Linux only; on a Mac the kernel import fails and training crashes. The
    GPU run never uses this: it materialises the full logits, which is fine for
    a 135M smoke model and far too much memory for 7B.
    """

    @staticmethod
    def apply(hidden, weight, bias, targets, temperature, _chunk, softcap, logit_scale, outputs):
        logits = hidden @ weight.T
        if bias is not None:
            logits = logits + bias
        logits = logits * logit_scale
        if softcap:
            logits = softcap * torch.tanh(logits / softcap)
        logits = logits / temperature
        logp = torch.log_softmax(logits.float(), dim=-1)
        picked = logp.gather(-1, targets.unsqueeze(-1)).squeeze(-1)
        probs = logp.exp()
        extra = {
            "entropy": lambda: -(probs * logp).sum(-1),
            "log_sum_sq_probs": lambda: torch.logsumexp(2 * logp, dim=-1),
            "mean_logits": lambda: logits.mean(-1),
            "is_top1": lambda: logits.argmax(-1) == targets,
        }
        return (picked, *((extra[name]().detach() if name in outputs else None) for name in extra))


def _patch_for_cpu_smoke() -> None:
    import trl.trainer.utils as trl_utils

    if getattr(trl_utils, "_ChunkedLogProbFunction", None) is None:
        trl_utils._ChunkedLogProbFunction = _EagerLogProbs


def load_base(name: str, smoke: bool):
    tokenizer = AutoTokenizer.from_pretrained(name)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    if smoke:
        model = AutoModelForCausalLM.from_pretrained(name, dtype=torch.float32)
        return model, tokenizer
    quant = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_use_double_quant=True,
        bnb_4bit_compute_dtype=torch.bfloat16,
    )
    model = AutoModelForCausalLM.from_pretrained(
        name, quantization_config=quant, dtype=torch.bfloat16, device_map="auto"
    )
    model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True)
    return model, tokenizer


def datasets_for(kind: str, data_dir: Path, smoke: bool):
    files = {"train": str(data_dir / f"{kind}_train.jsonl"), "eval": str(data_dir / f"{kind}_eval.jsonl")}
    ds = load_dataset("json", data_files=files)
    keep = {"sft": ["prompt", "completion"], "dpo": ["prompt", "chosen", "rejected"]}[kind]
    ds = ds.remove_columns([c for c in ds["train"].column_names if c not in keep])
    if smoke:
        ds["train"] = ds["train"].select(range(min(8, len(ds["train"]))))
        ds["eval"] = ds["eval"].select(range(min(4, len(ds["eval"]))))
    return ds["train"], ds["eval"]


def common_args(args: argparse.Namespace) -> dict:
    smoke = args.smoke
    return {
        "output_dir": args.out,
        "max_length": 2048 if smoke else args.max_length,
        "bf16": not smoke,
        "gradient_checkpointing": not smoke,
        "optim": "adamw_torch" if smoke else "paged_adamw_8bit",
        "lr_scheduler_type": "cosine",
        "warmup_steps": 0.03,  # a float below 1 is a ratio of total steps (transformers 5)
        "logging_steps": 1 if smoke else 10,
        "eval_strategy": "steps",
        "eval_steps": 2 if smoke else 50,
        "save_strategy": "steps",
        "save_steps": 2 if smoke else 50,
        "save_total_limit": 3,
        "load_best_model_at_end": True,
        "metric_for_best_model": "eval_loss",
        "max_steps": 2 if smoke else -1,
        "report_to": "none" if smoke else args.report_to,
        "run_name": f"papermind-writer-{args.stage}",
        "use_cpu": smoke,
    }


def train_sft(args: argparse.Namespace) -> None:
    model, tokenizer = load_base(args.base, args.smoke)
    train, evaluation = datasets_for("sft", args.data, args.smoke)
    lora = LoraConfig(r=16, lora_alpha=32, lora_dropout=0.05, target_modules=TARGETS, task_type="CAUSAL_LM")
    config = SFTConfig(
        **common_args(args),
        num_train_epochs=args.epochs or 3,
        learning_rate=args.lr or 2e-4,
        per_device_train_batch_size=1 if args.smoke else 4,
        gradient_accumulation_steps=1 if args.smoke else 4,
        completion_only_loss=True,  # learn the answer, not to reproduce the prompt
    )
    trainer = SFTTrainer(
        model=model,
        args=config,
        train_dataset=train,
        eval_dataset=evaluation,
        processing_class=tokenizer,
        peft_config=lora,
    )
    trainer.model.print_trainable_parameters()
    trainer.train()
    finish(trainer, tokenizer, args)


def train_dpo(args: argparse.Namespace) -> None:
    if not args.sft_adapter:
        raise SystemExit("--stage dpo needs --sft-adapter (the SFT output directory or hub id)")
    model, tokenizer = load_base(args.base, args.smoke)
    # The SFT adapter is the policy we keep training; trl copies it into a frozen
    # "ref" adapter for the reference log-probs, so the reference is the SFT model.
    model = PeftModel.from_pretrained(model, args.sft_adapter, is_trainable=True)
    train, evaluation = datasets_for("dpo", args.data, args.smoke)
    config = DPOConfig(
        **common_args(args),
        num_train_epochs=args.epochs or 1,
        learning_rate=args.lr or 5e-6,
        beta=0.1,
        per_device_train_batch_size=1 if args.smoke else 2,
        gradient_accumulation_steps=1 if args.smoke else 8,
    )
    trainer = DPOTrainer(
        model=model,
        args=config,
        train_dataset=train,
        eval_dataset=evaluation,
        processing_class=tokenizer,
    )
    trainer.train()
    finish(trainer, tokenizer, args)


def finish(trainer, tokenizer, args: argparse.Namespace) -> None:
    trainer.save_model(args.out)
    tokenizer.save_pretrained(args.out)
    if args.push_to_hub:
        # Only the adapter (~55 MB for r=16 on a 7B), never the base model.
        trainer.model.push_to_hub(args.push_to_hub)
        tokenizer.push_to_hub(args.push_to_hub)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--stage", choices=["sft", "dpo"], required=True)
    p.add_argument("--base", default=None, help=f"base model (default {BASE})")
    p.add_argument("--data", type=Path, default=DATA)
    p.add_argument("--out", required=True)
    p.add_argument("--sft-adapter", help="dpo: the SFT adapter to start from")
    p.add_argument("--epochs", type=float)
    p.add_argument("--lr", type=float)
    p.add_argument("--max-length", type=int, default=3072, help="prompt + answer tokens (default 3072)")
    p.add_argument("--report-to", default="wandb")
    p.add_argument(
        "--push-to-hub", metavar="REPO", help="push the finished adapter, e.g. you/papermind-writer"
    )
    p.add_argument("--smoke", action="store_true", help="tiny model on CPU, 2 steps: checks the script runs")
    args = p.parse_args(argv)
    args.base = args.base or (SMOKE_BASE if args.smoke else BASE)
    if args.smoke:
        _patch_for_cpu_smoke()
    os.environ.setdefault("WANDB_PROJECT", "papermind")

    (train_sft if args.stage == "sft" else train_dpo)(args)
    print(f"saved adapter to {args.out}")


if __name__ == "__main__":
    main()
