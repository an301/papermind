"""Starter questions for an open paper, per reader level.

Generated once per (paper, level) with a low-effort structured-output call
grounded in the abstract and outline, then cached next to the paper as
``suggestions.{level}.json``: the paper never changes, so neither do the
questions, and the panel should not cost a model call every time it opens.

Without an API key, or when the call fails, the questions come from the
section titles instead. Those are not cached, so the real ones replace them as
soon as generation can succeed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field

from papermind.config import Effort
from papermind.schemas.paper import PaperMeta, Section
from papermind.schemas.tools import Level

log = logging.getLogger(__name__)

COUNT = 4
MAX_QUESTION_CHARS = 140


class ParseResult(Protocol):
    parsed: BaseModel | None


class StructuredLLM(Protocol):
    """``papermind.llm.client.LLM.parse``."""

    async def parse(
        self,
        *,
        system: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        schema: type[BaseModel],
        effort: str,
        max_tokens: int,
    ) -> Any: ...


class SuggestedQuestions(BaseModel):
    # No minItems/maxItems: structured outputs strip unsupported array bounds
    # and the SDK would then reject a 3-item answer client-side. Count is
    # enforced below, where a short list is padded instead of failing.
    questions: list[str] = Field(description=f"exactly {COUNT} questions, each one short sentence")


_LEVEL_BRIEF: dict[Level, str] = {
    "beginner": (
        "The reader is new to the field. Ask about the problem the paper solves, what its key terms mean, "
        "and the intuition behind the main idea. No jargon the abstract does not define."
    ),
    "intermediate": (
        "The reader knows the basics of machine learning. Ask how the main method works, why specific "
        "design choices were made, and how it compares to the approach it improves on."
    ),
    "expert": (
        "The reader is a researcher in the area. Ask about assumptions, limitations, ablations, "
        "failure modes, and how the results would change under different conditions."
    ),
}

SYSTEM = (
    "You write the starter questions shown beside a research paper in a reading app. "
    f"Write exactly {COUNT} questions a reader at the stated level would want answered by this paper. "
    "Each question must be specific to this paper (name its method, component, or result), answerable "
    f"from the paper itself, under {MAX_QUESTION_CHARS} characters, and different in topic from the "
    "others. Plain questions only: no numbering, no quotes, no preamble."
)

# Sections whose titles make poor questions.
_SKIP_SECTION = re.compile(
    r"^(abstract|introduction|related work|background|conclusions?|discussion|acknowledge?ments?|"
    r"references|bibliography|appendix|appendices|supplementary.*|author contributions?)$",
    re.IGNORECASE,
)


def outline(meta: PaperMeta, limit: int = 40) -> str:
    lines = []
    for section in meta.sections[:limit]:
        indent = "  " * (section.level - 1)
        number = f"{section.number} " if section.number else ""
        lines.append(f"{indent}{number}{section.title}")
    return "\n".join(lines)


def prompt(meta: PaperMeta, level: Level) -> str:
    abstract = (meta.abstract or "").strip() or "(no abstract)"
    return (
        f"Reader level: {level}. {_LEVEL_BRIEF[level]}\n\n"
        f"Title: {meta.title}\n\nAbstract:\n{abstract}\n\nOutline:\n{outline(meta) or '(no outline)'}"
    )


def clean(questions: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for q in questions:
        text = re.sub(r"^\s*(?:\d+[.)]|[-*•])\s*", "", q).strip().strip('"').strip()
        if not text or len(text) > MAX_QUESTION_CHARS:
            continue
        if not text.endswith("?"):
            text += "?"
        key = text.casefold()
        if key not in seen:
            seen.add(key)
            out.append(text)
    return out


def _topical_sections(meta: PaperMeta) -> list[Section]:
    picked = [s for s in meta.sections if not _SKIP_SECTION.match(s.title.strip())]
    top = [s for s in picked if s.level == 1]
    return (top or picked)[:COUNT]


def fallback_questions(meta: PaperMeta, level: Level) -> list[str]:
    """Deterministic questions from the section titles, for when the model cannot be asked."""
    titles = [s.title.strip() for s in _topical_sections(meta)]
    if level == "beginner":
        templates = ["What does “{t}” mean here, in plain terms?", "Why does the paper need “{t}”?"]
        opening = ["What problem is this paper trying to solve?"]
    elif level == "expert":
        templates = [
            "What are the key design choices in “{t}”, and what alternatives were possible?",
            "What assumptions or limitations does “{t}” rely on?",
        ]
        opening = ["What is the paper's main contribution over prior work?"]
    else:
        templates = ["How does “{t}” work?", "What role does “{t}” play in the overall method?"]
        opening = ["What is the main idea of this paper?"]
    questions = list(opening)
    for i, title in enumerate(titles):
        questions.append(templates[i % len(templates)].format(t=title))
    generic = [
        "What are the main results, and how were they measured?",
        "What are the limitations the authors acknowledge?",
        "How does this approach differ from earlier methods?",
    ]
    return clean(questions + generic)[:COUNT]


class SuggestionService:
    def __init__(self, llm: StructuredLLM | None, *, effort: Effort = "low", max_tokens: int = 4000) -> None:
        self._llm = llm
        self._effort = effort
        self._max_tokens = max_tokens
        # One generation per (paper, level) even when the panel opens in two
        # tabs at once; the second request waits and then reads the cache.
        self._locks: dict[tuple[str, Level], asyncio.Lock] = {}

    async def get(self, meta: PaperMeta, level: Level, cache_dir: Path) -> list[str]:
        path = cache_dir / f"suggestions.{level}.json"
        cached = await asyncio.to_thread(_read_cache, path)
        if cached:
            return cached
        if self._llm is None:
            return fallback_questions(meta, level)
        lock = self._locks.setdefault((meta.paper_id, level), asyncio.Lock())
        async with lock:
            cached = await asyncio.to_thread(_read_cache, path)
            if cached:
                return cached
            try:
                questions = await self._generate(meta, level)
            except Exception:
                log.warning("suggestion generation failed for %s/%s", meta.paper_id, level, exc_info=True)
                return fallback_questions(meta, level)
            await asyncio.to_thread(_write_cache, path, questions)
            return questions

    async def _generate(self, meta: PaperMeta, level: Level) -> list[str]:
        assert self._llm is not None
        result: ParseResult = await self._llm.parse(
            system=[{"type": "text", "text": SYSTEM}],
            messages=[{"role": "user", "content": prompt(meta, level)}],
            schema=SuggestedQuestions,
            effort=self._effort,
            max_tokens=self._max_tokens,
        )
        if not isinstance(result.parsed, SuggestedQuestions):
            raise ValueError("model returned no structured output")
        questions = clean(result.parsed.questions)
        if not questions:
            raise ValueError("model returned no usable questions")
        # Pad a short answer from the fallback rather than show three chips.
        for extra in fallback_questions(meta, level):
            if len(questions) >= COUNT:
                break
            if extra.casefold() not in {q.casefold() for q in questions}:
                questions.append(extra)
        return questions[:COUNT]


def _read_cache(path: Path) -> list[str] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    questions = data.get("questions") if isinstance(data, dict) else None
    if isinstance(questions, list) and questions and all(isinstance(q, str) for q in questions):
        return questions
    return None


def _write_cache(path: Path, questions: list[str]) -> None:
    if not path.parent.is_dir():
        # The paper was deleted while we were generating; do not recreate its directory.
        return
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"questions": questions}, indent=2), encoding="utf-8")
    os.replace(tmp, path)
