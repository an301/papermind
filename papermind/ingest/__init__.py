"""Paper ingestion: arXiv id -> section-aligned, stably-identified JSON chunks.

    from papermind.ingest import ingest_paper

CLI:

    python -m papermind.ingest --arxiv-id 1706.03762

Pipeline stages, each in its own module so they can be tested alone:

    arxiv.py     download + version resolution + PDF cache
    extract.py   PyMuPDF -> ordered lines with typography (columns, hyphens)
    sections.py  headings -> the paper's real section structure
    chunker.py   sections -> chunks (paragraph packing, char-split backstop)
    ids.py       stable, human-readable chunk IDs
    tokens.py    token counting (fast estimate, or exact via the API)
    store.py     atomic JSON writes + manifest
    pipeline.py  orchestration
"""

from papermind.ingest.models import Chunk, IngestResult, PaperMeta
from papermind.ingest.pipeline import DEFAULT_MAX_CHUNK_TOKENS, ingest_paper

__all__ = [
    "DEFAULT_MAX_CHUNK_TOKENS",
    "Chunk",
    "IngestResult",
    "PaperMeta",
    "ingest_paper",
]
