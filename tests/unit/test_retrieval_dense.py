"""The dense arm, with a fake embedder: windows, max-over-windows scoring, and the vector cache."""

import re
from itertools import pairwise
from pathlib import Path

import numpy as np
import pytest

from papermind.retrieval.dense import (
    BGE_QUERY_INSTRUCTION,
    WINDOW_OVERLAP,
    WINDOW_TOKENS,
    DenseIndex,
    EmbeddingCache,
    FastEmbedEmbedder,
    WindowVectors,
    embed_windows,
    fingerprint,
    load_or_embed,
    token_windows,
)
from tests.unit.conftest import HashEmbedder

# ---------------------------------------------------------------- token windows


def fake_tokenize(text: str) -> tuple[list[tuple[int, int]], list[int | None]]:
    """Each word split into 3-character sub-word tokens, like a WordPiece tokenizer would."""
    offsets: list[tuple[int, int]] = []
    word_ids: list[int | None] = []
    for word_index, match in enumerate(re.finditer(r"\S+", text)):
        for start in range(match.start(), match.end(), 3):
            offsets.append((start, min(start + 3, match.end())))
            word_ids.append(word_index)
    return offsets, word_ids


def test_short_text_is_one_window():
    text = "a short passage"
    offsets, word_ids = fake_tokenize(text)
    assert token_windows(text, offsets, word_ids, window=50, overlap=5) == [text]


def test_windows_cover_everything_overlap_and_never_split_a_word():
    words = [f"word{i:03d}" for i in range(60)]  # 3 tokens each
    text = " ".join(words)
    offsets, word_ids = fake_tokenize(text)
    windows = token_windows(text, offsets, word_ids, window=30, overlap=6)
    assert len(windows) > 1
    for window in windows:
        pieces = window.split()
        assert all(piece in words for piece in pieces), window  # whole words only
        assert len(fake_tokenize(window)[0]) <= 30
    seen = [w for window in windows for w in window.split()]
    assert set(seen) == set(words)
    for left, right in pairwise(windows):
        assert set(left.split()) & set(right.split()), "consecutive windows overlap"
    assert windows[0].startswith("word000") and windows[-1].endswith("word059")


def test_a_word_longer_than_the_window_still_makes_progress():
    text = "x" * 40 + " tail"
    offsets, word_ids = fake_tokenize(text)
    windows = token_windows(text, offsets, word_ids, window=5, overlap=2)
    assert windows[-1].endswith("tail")
    assert len(windows) < 40


def test_window_constants_fit_the_encoder():
    # bge reads 512 positions including [CLS] and [SEP].
    assert WINDOW_TOKENS + 2 <= 512
    assert 0 < WINDOW_OVERLAP < WINDOW_TOKENS


# ---------------------------------------------------------------- the embedder wrapper (no model load)


def test_fastembed_embedder_is_lazy_and_validates_windows(tmp_path):
    embedder = FastEmbedEmbedder("BAAI/bge-base-en-v1.5", tmp_path)
    assert embedder._model is None  # constructing it loads nothing
    assert embedder.query_instruction == BGE_QUERY_INSTRUCTION
    assert embedder.name == f"BAAI/bge-base-en-v1.5|w{WINDOW_TOKENS}o{WINDOW_OVERLAP}"
    with pytest.raises(ValueError):
        FastEmbedEmbedder("BAAI/bge-base-en-v1.5", tmp_path, window_tokens=10, overlap_tokens=10)


def test_query_instruction_is_only_for_bge(tmp_path):
    assert FastEmbedEmbedder("sentence-transformers/all-MiniLM-L6-v2", tmp_path).query_instruction == ""


def test_query_instruction_text():
    assert BGE_QUERY_INSTRUCTION == "Represent this sentence for searching relevant passages: "


# ---------------------------------------------------------------- index


def unit(*values: float) -> np.ndarray:
    vector = np.asarray(values, dtype=np.float32)
    return vector / np.linalg.norm(vector)


def test_chunk_scores_the_best_of_its_windows():
    windows = WindowVectors(
        vectors=np.stack([unit(1, 0, 0), unit(0, 1, 0), unit(0.6, 0.8, 0), unit(0, 0, 1)]),
        owners=np.array([0, 0, 1, 2]),
    )
    index = DenseIndex(windows, size=4)
    scores = index.scores(unit(0, 1, 0))
    assert scores[0] == pytest.approx(1.0)  # its second window is the query
    assert scores[1] == pytest.approx(0.8)
    assert scores[2] == pytest.approx(0.0, abs=1e-6)
    assert scores[3] == -np.inf  # no windows at all (empty chunk)


def test_query_vectors_are_normalised_before_search():
    windows = WindowVectors(vectors=np.stack([unit(1, 0)]), owners=np.array([0]))
    assert DenseIndex(windows, size=1).scores(np.array([5.0, 0.0]))[0] == pytest.approx(1.0)


def test_empty_index_scores_minus_infinity():
    index = DenseIndex(WindowVectors(vectors=np.zeros((0, 4), np.float32), owners=np.zeros(0, np.int64)), 2)
    assert index.scores(unit(1, 0, 0, 0)).tolist() == [-np.inf, -np.inf]


def test_window_vectors_need_one_owner_each():
    with pytest.raises(ValueError):
        WindowVectors(vectors=np.zeros((2, 3), np.float32), owners=np.array([0]))


# ---------------------------------------------------------------- embedding


async def test_embed_windows_maps_windows_to_chunks_and_reports_progress():
    embedder = HashEmbedder(window_words=10, overlap_words=2)
    texts = ["short chunk", " ".join(f"w{i}" for i in range(25)), "   ", "another short one"]
    progress: list[float] = []

    async def report(fraction: float) -> None:
        progress.append(fraction)

    windows = await embed_windows(embedder, texts, on_progress=report, batch_size=2)
    owners = windows.owners.tolist()
    assert owners[0] == 0 and owners[-1] == 3
    assert owners.count(1) == len(embedder.windows(texts[1])) > 1
    assert 2 not in owners  # a blank chunk has no windows
    assert windows.vectors.shape == (len(owners), embedder.dim)
    assert np.allclose(np.linalg.norm(windows.vectors, axis=1), 1.0, atol=1e-5)
    assert progress == sorted(progress) and progress[-1] == 1.0


async def test_embed_windows_of_nothing():
    windows = await embed_windows(HashEmbedder(), [])
    assert windows.vectors.shape == (0, 64) and len(windows.owners) == 0


def test_fingerprint_covers_model_and_every_text():
    base = fingerprint("model-a", ["one", "two"])
    assert base == fingerprint("model-a", ["one", "two"])
    assert base != fingerprint("model-b", ["one", "two"])
    assert base != fingerprint("model-a", ["one", "two!"])
    # Boundaries count: ['ab', 'c'] is not ['a', 'bc'].
    assert fingerprint("m", ["ab", "c"]) != fingerprint("m", ["a", "bc"])


# ---------------------------------------------------------------- the on-disk cache


def sample_windows() -> WindowVectors:
    return WindowVectors(vectors=np.stack([unit(1, 0), unit(0, 1)]), owners=np.array([0, 1]))


def test_cache_round_trip_and_stale_entries_are_removed(tmp_path: Path):
    cache = EmbeddingCache(tmp_path)
    cache.save("p1", "old", sample_windows())
    cache.save("p1", "new", sample_windows())
    assert sorted(p.name for p in (tmp_path / "p1").iterdir()) == ["new.npz"]
    loaded = cache.load("p1", "new")
    assert loaded is not None
    assert np.array_equal(loaded.owners, [0, 1]) and loaded.vectors.dtype == np.float32
    assert cache.load("p1", "old") is None
    assert cache.load("missing", "new") is None


def test_corrupt_cache_file_is_a_miss(tmp_path: Path):
    cache = EmbeddingCache(tmp_path)
    cache.path("p1", "k").parent.mkdir(parents=True)
    cache.path("p1", "k").write_bytes(b"not an npz")
    assert cache.load("p1", "k") is None


async def test_load_or_embed_uses_the_cache(tmp_path: Path):
    embedder = HashEmbedder()
    cache = EmbeddingCache(tmp_path)
    texts = ["attention is all you need", "batch normalization"]
    first = await load_or_embed(embedder, cache, "p1", texts)
    calls = embedder.passage_calls
    second = await load_or_embed(embedder, cache, "p1", texts)
    assert embedder.passage_calls == calls  # served from disk
    assert np.allclose(first.vectors, second.vectors)
    await load_or_embed(embedder, cache, "p1", [*texts, "a new chunk"])
    assert embedder.passage_calls > calls  # different texts, different fingerprint


async def test_load_or_embed_without_a_cache(tmp_path: Path):
    embedder = HashEmbedder()
    progress: list[float] = []

    async def report(fraction: float) -> None:
        progress.append(fraction)

    windows = await load_or_embed(embedder, None, "p1", ["one chunk"], on_progress=report)
    assert windows.owners.tolist() == [0] and progress[-1] == 1.0
