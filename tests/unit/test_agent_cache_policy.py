"""The prompt-cache contract: the prefix is byte-stable and breakpoints sit where the plan says."""

from __future__ import annotations

import json
import re
from typing import Any

from papermind.agent.cache_policy import (
    agent_request,
    load_prompt,
    mastery_system,
    paper_card,
    writer_request,
)
from papermind.agent.loop import build_messages
from papermind.agent.registry import build_registry
from tests.unit.fakes import fake_settings, make_ctx, make_paper


def rendered_prefix(parts: Any) -> str:
    """tools + system, as they render ahead of the messages."""
    return json.dumps({"tools": parts.tools, "system": parts.system}, sort_keys=False)


def agent_parts(*, known: tuple[str, ...] = (), level: str = "beginner", caching: bool = True) -> Any:
    settings = fake_settings()
    ctx = make_ctx(level=level, known=known)
    return agent_request(
        tools=build_registry().anthropic_tools(),
        paper=ctx.paper,
        messages=build_messages(ctx, settings),
        caching=caching,
    )


def cache_markers(obj: Any) -> int:
    return json.dumps(obj).count('"cache_control"')


def test_breakpoint_plan_with_caching_on() -> None:
    parts = agent_parts()
    # Breakpoint 1 on the instructions, breakpoint 2 on the paper card, automatic on the tail.
    assert [b.get("cache_control") for b in parts.system] == [{"type": "ephemeral"}] * 2
    assert parts.system[0]["text"] == load_prompt("system")
    assert parts.system[1]["text"] == paper_card(make_paper())
    assert parts.cache is True
    assert cache_markers(parts.tools) == 0 and cache_markers(parts.messages) == 0
    # At most 4 breakpoints per request: 2 explicit + 1 automatic.
    assert cache_markers(parts.system) + int(parts.cache) <= 4


def test_no_cache_control_anywhere_when_caching_is_off() -> None:
    parts = agent_parts(caching=False)
    assert parts.cache is False
    assert cache_markers({"tools": parts.tools, "system": parts.system, "messages": parts.messages}) == 0
    writer = writer_request(paper=make_paper(), user_content="x", caching=False)
    assert cache_markers(writer.system) == 0 and writer.cache is False
    assert cache_markers(mastery_system(caching=False)) == 0


def test_same_inputs_render_identical_bytes() -> None:
    first, second = agent_parts(known=("Softmax",)), agent_parts(known=("Softmax",))
    assert json.dumps(first.__dict__) == json.dumps(second.__dict__)


def test_reader_state_never_touches_tools_or_system() -> None:
    novice = agent_parts(known=(), level="beginner")
    expert = agent_parts(known=("Softmax", "Dot Product", "Attention"), level="expert")
    assert rendered_prefix(novice) == rendered_prefix(expert)
    # It lives in the messages instead.
    assert novice.messages[-1] != expert.messages[-1]


def test_paper_card_is_deterministic_and_skips_volatile_fields() -> None:
    paper = make_paper()
    later = paper.model_copy(update={"chunk_count": 99, "ingested_at": paper.ingested_at.replace(year=2030)})
    assert paper_card(paper) == paper_card(later)
    card = paper_card(paper)
    assert "Title: Attention Is All You Need" in card
    assert "- §3.2.1 Scaled Dot-Product Attention (page 4, sec_3.2.1)" in card
    assert "## Abstract" in card


def test_writer_caches_its_system_blocks_but_not_its_one_off_message() -> None:
    parts = writer_request(paper=make_paper(), user_content="question", caching=True)
    assert parts.system[0]["text"] == load_prompt("writer")
    assert all(b["cache_control"] == {"type": "ephemeral"} for b in parts.system)
    assert parts.cache is False and parts.tools is None


def test_prompts_are_byte_stable_and_long_enough_to_cache() -> None:
    for name in ("system", "writer", "mastery"):
        text = load_prompt(name)
        assert load_prompt(name) is text  # read once per process
        assert not re.search(r"\b20\d\d-\d\d-\d\d\b", text), f"{name}.md contains a date"
        # No template placeholders: these files are sent as-is, never formatted.
        # (LaTeX such as \sqrt{d_k} follows a command name, so it does not match.)
        assert not re.search(r"(?<![\\\w])\{[a-z_]+\}", text), f"{name}.md contains a placeholder"
    # Opus 5.5 caches prefixes from 512 tokens; the agent's should be well past it
    # on its own (roughly 4 characters per token).
    assert len(load_prompt("system")) / 4 > 1200
    assert len(load_prompt("writer")) / 4 > 512
