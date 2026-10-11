# PaperMind

A research-paper reading agent. A reader opens a paper (arXiv link or their own
PDF), reads it in the app, and asks questions in a side panel. A hand-written
ReAct loop on hosted Claude retrieves the right passages (hybrid BM25 + dense
retrieval), checks a prerequisite concept graph for what this reader is missing,
optionally looks up cited papers on Semantic Scholar, then hands off to a writer
that explains at the reader's level and cites the passages it used.

- Implementation contract: `docs/architecture/ARCHITECTURE.md`
- Shared data models (single source of truth): `papermind/schemas/`
- Design system and UI spec: `docs/design/DESIGN.md`

## Hard constraints

1. No agent frameworks (no LangChain, LlamaIndex, or the SDK tool runner). The
   loop in `papermind/agent/loop.py` stays readable top to bottom.
2. Python 3.11, async throughout. CPU-bound work goes through `asyncio.to_thread`
   or lives in an MCP server process.
3. Unit tests run with no Redis, Neo4j, network, or API key. Tests that need
   services are marked `integration`; tests that spend API money are marked `eval`.
4. Settings only via `papermind/config.py`. Nothing else reads `os.environ`.
5. Comments explain the non-obvious why, not the what.
6. Prompt caching is a prefix match: the tool list is sorted and frozen, the
   system prompt is byte-stable, and volatile reader state (level, known concepts)
   goes in a mid-conversation `{"role": "system"}` message, never the system prompt.

## Anthropic API notes

- Model `claude-opus-5-5` (exact id, no date suffix). Thinking is always on for
  this model; control depth with `output_config={"effort": ...}`. Sending
  `budget_tokens`, `thinking: {"type": "disabled"}`, sampling params
  (`temperature`), forced `tool_choice`, or an assistant prefill returns a 400.
- Stream every call. Return all `tool_result` blocks for a step in one user
  message; failed tools return `is_error: true`, never a dropped block.
- Verify caching with `usage.cache_read_input_tokens`; zero across consecutive
  calls in one turn is a bug.
- `anthropic` 1.x is built on `httpx2`; use the `[aiohttp]` extra with
  `DefaultAioHttpClient()` for the async client.

## Commands

```bash
make install              # venv + Python deps + frontend deps
make services             # start local Redis + Neo4j (Homebrew)
make seed                 # load data/concepts into Neo4j
make dev                  # API on :8000 (spawns the three MCP servers over stdio)
make frontend             # Vite on :5173, proxies /api to :8000
make test                 # unit tests: no services, no network, no key
make test-int             # integration tests (Redis, Neo4j, network)
make eval-retrieval       # recall@5 ablation, no API key needed
make eval                 # full benchmark against the live API (spends money)
```

The local venv is `.venv` (Python 3.11 via uv). Neo4j dev credentials are
`neo4j` / `papermind-dev`.

## Working notes for agents

- Run anything that can take longer than about two minutes in the background
  and poll its log: benchmark ingestion (embedding runs at ~7 chunks/s on CPU),
  full Playwright screenshot runs, `npm install`, the dev servers, eval suites.
  A foreground command that is silent for three minutes gets the agent killed
  as stalled.
- Never run git commands that modify the working tree or history.
