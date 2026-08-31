"""JSON persistence for ingested papers.

Layout:

    data/
      pdfs/{versioned_id}.pdf      cached source, never rewritten in place
      papers/{paper_id}.json       metadata + chunks for one paper
      papers/manifest.json         index across papers

One file per paper, rather than one giant chunk file: it keeps a re-ingest to a
single-file diff, and makes it obvious in review when a paper's chunk IDs move.

Writes are atomic (temp file + rename). A half-written papers/*.json read back
by the eval harness would look like a paper that lost sections.
"""

from __future__ import annotations

import json
from pathlib import Path

from papermind.ingest.models import IngestResult


def paper_path(out_dir: Path, paper_id: str) -> Path:
    # ':' and '/' appear in legacy arXiv ids; keep filenames portable.
    return out_dir / f"{paper_id.replace('/', '_')}.json"


def _write_atomic(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)


def save_paper(out_dir: Path, result: IngestResult) -> Path:
    path = paper_path(out_dir, result.meta.paper_id)
    # indent=2 + sorted keys: these files get reviewed in diffs, and a chunk
    # boundary moving should be readable rather than a one-line blob.
    _write_atomic(path, json.dumps(result.model_dump(), indent=2, ensure_ascii=False) + "\n")
    return path


def load_paper(out_dir: Path, paper_id: str) -> IngestResult | None:
    path = paper_path(out_dir, paper_id)
    if not path.exists():
        return None
    return IngestResult.model_validate_json(path.read_text(encoding="utf-8"))


def update_manifest(out_dir: Path) -> Path:
    """Rebuild the cross-paper index from whatever is on disk.

    Derived, never authoritative -- so a manually deleted paper file cannot
    leave a stale entry behind.
    """
    entries = []
    for path in sorted(out_dir.glob("*.json")):
        if path.name == "manifest.json":
            continue
        result = IngestResult.model_validate_json(path.read_text(encoding="utf-8"))
        entries.append(
            {
                "paper_id": result.meta.paper_id,
                "versioned_id": result.meta.versioned_id,
                "title": result.meta.title,
                "n_chunks": result.n_chunks,
                "n_pages": result.meta.n_pages,
                "chunker_version": result.chunker_version,
                "pdf_sha256": result.meta.pdf_sha256,
                "ingested_at": result.ingested_at,
            }
        )

    path = out_dir / "manifest.json"
    _write_atomic(
        path,
        json.dumps({"papers": entries, "n_papers": len(entries)}, indent=2, ensure_ascii=False)
        + "\n",
    )
    return path
