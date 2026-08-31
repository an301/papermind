"""Token counting for chunk budgeting and for the stored `token_count` field.

Two counters, because the two uses have different requirements:

- `estimate_tokens` runs during chunking, where a count is needed for every
  candidate split of every section -- thousands of calls. It must be local and
  instant, so it is a heuristic, accurate to roughly +/-15% on English prose.
- `AnthropicTokenCounter` produces exact counts via `messages.count_tokens`,
  used to refine the stored field when `--exact-tokens` is passed. One network
  call per chunk, so it is opt-in.

Deliberately no tiktoken: it is OpenAI's tokenizer and would give a confidently
wrong number for a Claude context budget.

Whichever counter ran is recorded as `token_counter` in the output, so a
downstream budget calculation can tell an estimate from a measurement.
"""

from __future__ import annotations

# Chars per token for English academic prose. Math-heavy text runs denser
# (more tokens per char), so this errs slightly low there -- which is the safe
# direction for a chunk budget: it splits a little earlier than strictly needed.
_CHARS_PER_TOKEN = 4.0


def estimate_tokens(text: str) -> int:
    """Fast local estimate. Never returns 0 for non-empty text."""
    if not text:
        return 0
    return max(1, round(len(text) / _CHARS_PER_TOKEN))


def estimate_tokens_from_chars(char_count: int) -> int:
    """Same estimate, from a length alone -- lets the chunker budget a join
    without materialising the joined string on every candidate paragraph."""
    if char_count <= 0:
        return 0
    return max(1, round(char_count / _CHARS_PER_TOKEN))


class AnthropicTokenCounter:
    """Exact counts via the Messages API `count_tokens` endpoint.

    Constructed only when `--exact-tokens` is requested, so importing this
    module never requires an API key.
    """

    def __init__(self, model: str, api_key: str | None = None) -> None:
        import anthropic  # imported lazily: ingestion works with no SDK configured

        self._client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        self._model = model

    def count(self, text: str) -> int:
        response = self._client.messages.count_tokens(
            model=self._model,
            messages=[{"role": "user", "content": text}],
        )
        return int(response.input_tokens)
