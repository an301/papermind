"""CLI: `python -m papermind.ingest --arxiv-id 1706.03762`.

Ingests one or more papers. Failures are per-paper: a batch of eight does not
lose seven because one PDF is malformed, and the exit code still reports that
something went wrong.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import httpx

from papermind.ingest.arxiv import ArxivError, make_client
from papermind.ingest.ids import CHUNKER_VERSION
from papermind.ingest.pipeline import (
    DEFAULT_MAX_CHUNK_TOKENS,
    apply_exact_token_counts,
    ingest_paper,
)
from papermind.ingest.store import save_paper, update_manifest

DEFAULT_OUT_DIR = Path("data/papers")
DEFAULT_PDF_DIR = Path("data/pdfs")
DEFAULT_TOKEN_MODEL = "claude-opus-5"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m papermind.ingest",
        description="Download arXiv papers and chunk them by section into JSON.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python -m papermind.ingest --arxiv-id 1706.03762\n"
            "  python -m papermind.ingest --arxiv-id 1706.03762 --arxiv-id 1810.04805\n"
            "  python -m papermind.ingest --from-file papers.txt\n"
            "\n"
            "Pin a version (--arxiv-id 1706.03762v7) to reproduce a run exactly.\n"
        ),
    )
    parser.add_argument(
        "--arxiv-id",
        action="append",
        default=[],
        metavar="ID",
        help="arXiv id, optionally versioned. Repeat for several papers.",
    )
    parser.add_argument(
        "--from-file",
        type=Path,
        metavar="PATH",
        help="File of arXiv ids, one per line; '#' comments allowed.",
    )
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--pdf-dir", type=Path, default=DEFAULT_PDF_DIR)
    parser.add_argument(
        "--max-chunk-tokens",
        type=int,
        default=DEFAULT_MAX_CHUNK_TOKENS,
        metavar="N",
        help=f"Split sections larger than this (default: {DEFAULT_MAX_CHUNK_TOKENS}). "
        "Changing it changes chunk boundaries and therefore part-suffixed IDs.",
    )
    parser.add_argument(
        "--exact-tokens",
        action="store_true",
        help="Replace estimated token counts with exact ones via the Anthropic "
        "API (one call per chunk; needs ANTHROPIC_API_KEY).",
    )
    parser.add_argument(
        "--token-model",
        default=DEFAULT_TOKEN_MODEL,
        help=f"Model whose tokenizer --exact-tokens uses (default: {DEFAULT_TOKEN_MODEL}).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download the PDF even if it is cached.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and report, but write nothing.",
    )
    parser.add_argument("--quiet", action="store_true")
    return parser


def collect_ids(args: argparse.Namespace) -> list[str]:
    ids: list[str] = list(args.arxiv_id)
    if args.from_file:
        for line in args.from_file.read_text(encoding="utf-8").splitlines():
            entry = line.split("#", 1)[0].strip()
            if entry:
                ids.append(entry)
    # De-duplicate, preserving order, so a repeated id is not fetched twice.
    return list(dict.fromkeys(ids))


async def run(args: argparse.Namespace) -> int:
    ids = collect_ids(args)
    if not ids:
        print("no arXiv ids given (use --arxiv-id or --from-file)", file=sys.stderr)
        return 2

    def log(message: str) -> None:
        if not args.quiet:
            print(message)

    failures = 0
    async with make_client() as client:
        for raw_id in ids:
            try:
                result = await ingest_paper(
                    raw_id,
                    client=client,
                    pdf_dir=args.pdf_dir,
                    max_chunk_tokens=args.max_chunk_tokens,
                    force=args.force,
                )
                if args.exact_tokens:
                    log(f"  counting tokens via {args.token_model} ({result.n_chunks} calls)...")
                    result = apply_exact_token_counts(result, args.token_model)

                total_tokens = sum(c.token_count for c in result.chunks)
                split = sum(1 for c in result.chunks if c.n_parts > 1)
                log(
                    f"{result.meta.versioned_id}  {result.meta.title[:58]}\n"
                    f"  {result.meta.n_pages} pages -> {result.n_chunks} chunks "
                    f"({split} from split sections), ~{total_tokens:,} tokens"
                )

                if args.dry_run:
                    log("  dry run: nothing written")
                else:
                    path = save_paper(args.out_dir, result)
                    log(f"  wrote {path}")

            except (ArxivError, httpx.HTTPError, OSError) as exc:
                failures += 1
                print(f"{raw_id}: {exc}", file=sys.stderr)

    if not args.dry_run and failures < len(ids):
        manifest = update_manifest(args.out_dir)
        log(f"manifest: {manifest}")

    log(f"chunker_version={CHUNKER_VERSION}  ok={len(ids) - failures}/{len(ids)}")
    return 1 if failures else 0


def main() -> int:
    return asyncio.run(run(build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
