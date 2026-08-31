"""Hosted-model client for agent reasoning (Anthropic API).

Wraps `AsyncAnthropic`. Owns retry/timeout policy and usage accounting; knows
nothing about papers or concepts.

Notes for whoever implements this:
- Model is `claude-opus-5` (see `.env.example`). Do not append a date suffix.
- Use `thinking={"type": "adaptive"}`; `budget_tokens` is rejected with a 400
  on this model family.
- Tune cost/latency with `output_config={"effort": ...}`, not by downgrading
  the model.
- Install the `anthropic[aiohttp]` extra and pass `DefaultAioHttpClient()` --
  the default httpx2 backend is measurably slower under concurrent sessions.
- Stream. A ReAct turn over a full paper can exceed the non-streaming HTTP
  timeout, and we need the progress events anyway.
"""
