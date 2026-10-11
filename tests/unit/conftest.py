"""Shared unit-test fixtures: synthetic PDFs and a fake embedding model.

Nothing here is autouse; tests opt in by naming a fixture.
"""

from __future__ import annotations

import hashlib
import re
import textwrap
from collections.abc import Sequence
from datetime import UTC, datetime

import numpy as np
import pymupdf
import pytest

from papermind.schemas.paper import Chunk, PaperMeta, PaperRecord, Section

# ---------------------------------------------------------------- synthetic PDFs

LOREM = (
    "Neural networks learn representations of their inputs by adjusting weights with gradient descent. "
    "Each layer transforms the activations of the previous one and the final layer produces a prediction. "
)


class PdfBuilder:
    """A tiny layout DSL over PyMuPDF for building test PDFs with known structure."""

    def __init__(self, width: float = 612, height: float = 792) -> None:
        self.doc = pymupdf.open()
        self.width, self.height = width, height
        self.page: pymupdf.Page | None = None

    def new_page(self) -> pymupdf.Page:
        self.page = self.doc.new_page(width=self.width, height=self.height)
        return self.page

    def text(self, x: float, y: float, text: str, *, size: float = 10, bold: bool = False, rotate: int = 0) -> float:
        """Write one line at baseline ``y``; returns the x where it ends."""
        assert self.page is not None
        font = "tibo" if bold else "tiro"
        self.page.insert_text((x, y), text, fontsize=size, fontname=font, rotate=rotate)
        return x + pymupdf.get_text_length(text, fontname=font, fontsize=size)

    def lines(self, x: float, y: float, lines: Sequence[str], *, size: float = 10, leading: float = 12) -> float:
        """Write consecutive lines; returns the baseline after the last one."""
        for line in lines:
            self.text(x, y, line, size=size)
            y += leading
        return y

    def paragraph(self, x: float, y: float, text: str, *, chars: int = 52, size: float = 10) -> float:
        return self.lines(x, y, textwrap.wrap(text, chars), size=size)

    def superscript(self, x: float, y: float, base: str, script: str, *, size: float = 10) -> float:
        """'base' with a raised, smaller 'script' after it (an exponent or footnote mark)."""
        end = self.text(x, y, base, size=size)
        return self.text(end, y - 0.35 * size, script, size=0.7 * size)

    def rect(self, x0: float, y0: float, x1: float, y1: float) -> None:
        assert self.page is not None
        self.page.draw_rect(pymupdf.Rect(x0, y0, x1, y1), color=(0, 0, 0), width=0.8)

    def furniture(self, number: int) -> None:
        """A running head and a centred page number, as every page of a preprint has."""
        self.text(72, 40, "Preprint. Under review at a synthetic venue.", size=8)
        self.text(self.width / 2 - 3, 760, str(number), size=9)

    def bytes(self, **save: object) -> bytes:
        return self.doc.tobytes(**save)


def build_two_column_paper() -> bytes:
    """Two pages, two columns, with every layout feature the extractor must handle.

    Page 1: rotated arXiv stamp, full-width title and authors, then columns:
    left = Abstract, 1 Introduction (a TeX hyphenation 'repre-/sentation', a
    real compound 'self-/attention', an exponent n^2, a figure with a caption
    between two paragraphs); right = 2 Method, 2.1 Scaled Attention.
    Page 2: 3 Experiments with a table row that looks like a heading, an
    equation number, 4 Conclusion, References.
    Both pages carry a running head and a page number.
    """
    b = PdfBuilder()
    left, right = 72.0, 320.0

    b.new_page()
    b.furniture(1)
    b.text(22, 640, "arXiv:2401.00001v1 [cs.LG] 1 Jan 2024", size=20, rotate=90)
    b.text(150, 90, "Synthetic Layouts for Testing Extraction", size=17, bold=True)
    b.text(210, 112, "Ada Lovelace and Alan Turing", size=11)

    y = 150.0
    b.text(left + 80, y, "Abstract", size=12, bold=True)
    y = b.paragraph(left, y + 18, "We study self-attention in synthetic documents. " + LOREM)
    y += 10
    b.text(left, y, "1 Introduction", size=12, bold=True)
    y += 18
    y = b.lines(
        left,
        y,
        [
            "Dense models learn a repre-",
            "sentation of every token. Each token attends",
            "to every other token with self-attention, so the",
            "cost grows with the sequence length. We use self-",
            "attention throughout this synthetic paper.",
        ],
    )
    end = b.text(left, y, "The cost is O(n")
    end = b.text(end, y - 3.5, "2", size=7)
    b.text(end + 1, y, ") in the sequence length n.")
    y += 24
    b.rect(left + 20, y, left + 200, y + 60)
    y += 76
    b.text(left, y, "Figure 1: Overview of the synthetic pipeline.", size=9)
    y += 20
    y = b.paragraph(left, y, "After the figure the introduction continues with more prose. " + LOREM)

    y = 150.0
    b.text(right, y, "2 Method", size=12, bold=True)
    y = b.paragraph(right, y + 18, "The method section begins in the right column. " + LOREM)
    y += 10
    b.text(right, y, "2.1 Scaled Attention", size=10, bold=True)
    y += 16
    end = b.text(right, y, "We divide the dot products by the square root of d")
    b.text(end, y + 2, "k", size=7)
    y += 12
    y = b.paragraph(right, y, "so that the softmax does not saturate for large dimensions. " + LOREM)

    b.new_page()
    b.furniture(2)
    y = 80.0
    b.text(left, y, "3 Experiments", size=12, bold=True)
    y = b.paragraph(left, y + 18, "We report results on two synthetic benchmarks in Table 1. " + LOREM)
    y += 8
    b.text(left, y, "Table 1: Accuracy of each model.", size=9)
    y += 14
    b.text(left, y, "Model", size=9)
    b.text(left + 120, y, "Accuracy", size=9)
    y += 11
    b.text(left, y, "3.3", size=9, bold=True)
    b.text(left + 120, y, "28.4", size=9)
    # LaTeX leaves \textfloatsep (~20pt) between a float and the text after it.
    y += 30
    b.text(left + 40, y, "y = Wx + b")
    b.text(left + 200, y, "(3)")
    y += 20
    y = b.paragraph(left, y, "The equation above is the whole model. " + LOREM)

    y = 80.0
    b.text(right, y, "4 Conclusion", size=12, bold=True)
    y = b.paragraph(right, y + 18, "Synthetic layouts make extraction testable. " + LOREM)
    y += 10
    b.text(right, y, "References", size=12, bold=True)
    y += 18
    b.lines(right, y, ["[1] A. Lovelace. Notes on the analytical", "engine. 1843."])
    return b.bytes()


@pytest.fixture(scope="session")
def two_column_pdf() -> bytes:
    return build_two_column_paper()


@pytest.fixture
def pdf_builder() -> PdfBuilder:
    return PdfBuilder()


# ---------------------------------------------------------------- fake embedder

_WORD = re.compile(r"\w+")


class HashEmbedder:
    """Deterministic bag-of-words embeddings: no model download, no ONNX.

    Each word hashes to a fixed random unit direction; a text is the
    normalised sum of its words' directions. Texts sharing words are similar,
    which is all the dense-arm tests need. Windows are word-based so the
    windowing contract (overlap, max over windows) is exercised.
    """

    def __init__(self, dim: int = 64, window_words: int = 40, overlap_words: int = 8) -> None:
        self._dim = dim
        self.window_words = window_words
        self.overlap_words = overlap_words
        self.passage_calls = 0
        self.passages_embedded = 0
        self.query_calls = 0

    @property
    def name(self) -> str:
        return f"hash-{self._dim}|w{self.window_words}o{self.overlap_words}"

    @property
    def dim(self) -> int:
        return self._dim

    def _word_vector(self, word: str) -> np.ndarray:
        seed = int.from_bytes(hashlib.sha256(word.lower().encode()).digest()[:4], "little")
        vector = np.random.default_rng(seed).standard_normal(self._dim)
        return vector / np.linalg.norm(vector)

    def _embed(self, text: str) -> np.ndarray:
        words = _WORD.findall(text.lower())
        total = np.zeros(self._dim)
        for word in words:
            total += self._word_vector(word)
        norm = np.linalg.norm(total)
        if norm == 0:
            total[0], norm = 1.0, 1.0
        return (total / norm).astype(np.float32)

    def windows(self, text: str) -> list[str]:
        words = text.split()
        if len(words) <= self.window_words:
            return [text]
        step = self.window_words - self.overlap_words
        out = []
        for start in range(0, len(words), step):
            out.append(" ".join(words[start : start + self.window_words]))
            if start + self.window_words >= len(words):
                break
        return out

    def embed_passages(self, texts: Sequence[str]) -> np.ndarray:
        self.passage_calls += 1
        self.passages_embedded += len(texts)
        if not texts:
            return np.zeros((0, self._dim), dtype=np.float32)
        return np.stack([self._embed(t) for t in texts])

    def embed_query(self, text: str) -> np.ndarray:
        self.query_calls += 1
        return self._embed(text)


@pytest.fixture
def fake_embedder() -> HashEmbedder:
    return HashEmbedder()


# ---------------------------------------------------------------- paper records


def make_record(
    paper_id: str,
    sections: Sequence[tuple[str | None, str, str]],
    *,
    title: str = "A Synthetic Paper",
    chunker_version: str = "1",
) -> PaperRecord:
    """A PaperRecord with one chunk per (number, title, text) section."""
    from papermind.ingest.ids import disambiguate, section_id, section_key

    sids = disambiguate([section_id(number, name) for number, name, _ in sections])
    keys = disambiguate([section_key(number, name) for number, name, _ in sections])
    chunks = [
        Chunk(
            chunk_id=f"{paper_id}:{key}",
            paper_id=paper_id,
            section_id=sid,
            section_number=number,
            section_title=name,
            text=text,
            token_count=len(text) // 4,
            page_start=i // 3,
            page_end=i // 3,
        )
        for i, ((number, name, text), sid, key) in enumerate(zip(sections, sids, keys, strict=True))
    ]
    meta = PaperMeta(
        paper_id=paper_id,
        source="upload",
        title=title,
        num_pages=max(1, len(chunks) // 3 + 1),
        sections=[
            Section(section_id=sid, number=number, title=name, page=i // 3)
            for i, ((number, name, _), sid) in enumerate(zip(sections, sids, strict=True))
        ],
        chunk_count=len(chunks),
        chunker_version=chunker_version,
        ingested_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    return PaperRecord(meta=meta, chunks=chunks)


TRANSFORMER_LIKE: list[tuple[str | None, str, str]] = [
    (None, "Abstract", "We propose the Transformer, a model based solely on attention mechanisms."),
    ("1", "Introduction", "Recurrent neural networks have been the state of the art in sequence modeling."),
    ("3", "Model Architecture", "The encoder maps an input sequence to continuous representations."),
    (
        "3.2.1",
        "Scaled Dot-Product Attention",
        "We compute the dot products of the query with all keys, divide each by the square root of d_k, "
        "and apply a softmax function. For large values of d_k the dot products grow large in magnitude.",
    ),
    ("3.2.2", "Multi-Head Attention", "Multi-head attention projects queries, keys and values h times."),
    ("3.5", "Positional Encoding", "We add sinusoidal positional encodings to inject token order information."),
    (
        "5.2",
        "Hardware and Schedule",
        "We trained our models on one machine with 8 NVIDIA P100 GPUs for 12 hours; the big models took 3.5 days.",
    ),
    ("5.4", "Regularization", "We apply residual dropout and label smoothing during training."),
    (
        None,
        "References",
        "[1] Jimmy Ba. Layer normalization. 2016. [2] Bahdanau. Neural machine translation.",
    ),
]


@pytest.fixture
def transformer_like_record() -> PaperRecord:
    return make_record("1706.03762", TRANSFORMER_LIKE, title="Attention Is All You Need")
