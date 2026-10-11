# PaperMind architecture

PaperMind is a research-paper reading agent. A reader opens a paper and asks
questions beside it; the answer is calibrated to their expertise level, built on
the passages of the paper that actually answer the question, and scaffolded
around the prerequisites this particular reader is missing.

This document is the implementation contract. The shared data models live in
`papermind/schemas/` and are the single source of truth; the prose below says
how the modules use them.

```
Browser · React (Vercel)              reads the PDF, asks in a side panel
   │  POST /api/chat  (question + session_id)   ← SSE: typed ChatEvents
   ▼
FastAPI (EC2, uvicorn --workers N, stateless) ───── Redis: session:{id}, 24h TTL
   │
   ▼
Agent loop · hosted Claude  (papermind/agent/loop.py, hand-written ReAct)
   │  Pydantic-validated tool I/O, retry on malformed calls
   │  tool calls in one step run concurrently (asyncio.gather)
   ├── MCP retriever         BM25 + dense (FAISS) → RRF → top 5 chunks
   ├── MCP prereq_graph      Neo4j, ~350 concepts, BFS gap detection, KNOWS edges
   └── MCP semantic_scholar  lookup / references / citations, cached + rate-limited
   │
   │  compose_answer(plan, source_chunk_ids, …)  ← the hand-off
   ▼
Writer (papermind/agent/writer.py)    writes the explanation at the reader's level
   │  today: Claude with a level rubric · later: fine-tuned Mistral 7B adapter
   ▼
Mastery pass (papermind/agent/mastery.py) → KNOWS edges in Neo4j (with undo in the UI)
```

Two models, two jobs. The agent model decides *what context is needed*; the
writer decides *how to say it*. The writer sits behind a small `Writer`
protocol so the fine-tuned Mistral adapter can replace the Claude writer
without touching the loop. Mistral fine-tuning itself is out of scope here.

## Hard constraints

1. **No agent frameworks.** No LangChain, LlamaIndex, or SDK tool runner. The
   ReAct loop is hand-written in `papermind/agent/loop.py`, readable top to
   bottom (~100 lines of loop logic, excluding helpers).
2. **Python 3.11, async throughout.** No blocking I/O on the event loop.
   CPU-bound work (embedding, PDF extraction) runs in a thread
   (`asyncio.to_thread`) or lives in the MCP server process.
3. **Small, independently testable modules.** Unit tests run with no Redis, no
   Neo4j, no network, and no API key (fakes for each). Integration tests are
   marked `integration`; evals that spend API money are marked `eval`.
4. **Comment the non-obvious *why*,** not the what.
5. **Settings only via `papermind/config.py`.**

## Repository layout

```
papermind/
  config.py              settings (pydantic-settings); the only env reader
  main.py                create_app() + lifespan (start/stop resources)
  schemas/               paper.py tools.py events.py session.py api.py  -- pure data
  ingest/                arxiv.py extract.py sections.py chunker.py ids.py store.py pipeline.py __main__.py
  retrieval/             text.py bm25.py dense.py fusion.py index.py hybrid.py
  graph/                 normalize.py gaps.py store.py seed.py service.py testing.py
  scholar/               client.py cache.py
  memory/                session_store.py users.py
  llm/                   client.py pricing.py
  mcp_client/            manager.py
  agent/                 loop.py dispatch.py registry.py cache_policy.py writer.py mastery.py
                         labels.py trace.py prompts/{system.md,writer.md,mastery.md}
  api/                   deps.py errors.py sse.py routes/{health,papers,users,concepts,sessions,chat}.py
mcp_servers/
  retriever/server.py    prereq_graph/server.py    semantic_scholar/server.py
data/
  concepts/concepts.json  areas.json      seed for the prerequisite graph (tracked)
  papers/{paper_id}/      paper.json (tracked for benchmark papers) + source.pdf (ignored)
evals/
  benchmark/{papers.txt, questions.jsonl, hand_labels.csv}
  harness/{dataset.py, runner.py, scorers.py, judges.py, report.py, wandb_log.py, run.py}
  conftest.py test_retrieval.py test_agent.py
scripts/                  build_concept_graph.py seed_graph.py generate_benchmark.py ingest_benchmark.py
tests/unit/  tests/integration/
frontend/                 React + Vite + TS (see docs/design/DESIGN.md)
infra/                    docker-compose.yml, Dockerfile.api, Dockerfile.mcp, ec2/
```

## Module contracts

### Ingestion (`papermind/ingest`)

```python
async def ingest_arxiv(raw: str, *, on_progress: ProgressFn | None = None) -> PaperRecord
async def ingest_upload(pdf: bytes, filename: str, *, on_progress: ProgressFn | None = None) -> PaperRecord
ProgressFn = Callable[[IngestStage, float], Awaitable[None]]   # (stage, 0..1)

class PaperStore:                       # data/papers/{paper_id}/
    def __init__(self, papers_dir: Path) -> None
    def save(self, record: PaperRecord, pdf: bytes) -> None   # atomic writes
    def load(self, paper_id: str) -> PaperRecord | None
    def meta(self, paper_id: str) -> PaperMeta | None
    def pdf_path(self, paper_id: str) -> Path
    def list(self) -> list[PaperMeta]
    def delete(self, paper_id: str) -> None
    def chunk(self, paper_id: str, chunk_id: str) -> Chunk | None
```

* arXiv input normalisation accepts bare/versioned ids, abs/pdf URLs, old-style
  ids. The resolved version is recorded. Uploads get `paper_id = "up_" + sha256(pdf)[:12]`.
* Extraction (PyMuPDF): two-column reading order, running heads/page numbers
  dropped, hyphenation rejoined, ligatures NFKC-folded, whitespace spans joined
  (never filtered), captions moved to the end of their section, superscripts kept.
* Sectioning from typography (font size/weight) + numbering patterns; outline
  sections carry page + y for scroll-to.
* Chunking is section-first: a section that fits `max_chunk_tokens` is one
  chunk; otherwise pack paragraphs; a single over-long paragraph splits at a
  sentence boundary. Fragments under ~400 characters merge into a neighbour.
  Each chunk carries `boxes` (block-level rects) so the UI can highlight it.
* Chunk ids are eval labels: derived from section number + slugged title, never
  list position. `CHUNKER_VERSION` in `ids.py` bumps only when ids can move.

### Retrieval (`papermind/retrieval`)

```python
class HybridRetriever:
    async def search(self, query: str, paper_id: str, k: int = 5,
                     mode: RetrievalMode = "hybrid", section_filter: str | None = None) -> list[RetrievedChunk]
    async def bm25_only(self, query, paper_id, k=5) -> list[RetrievedChunk]
    async def dense_only(self, query, paper_id, k=5) -> list[RetrievedChunk]
    async def hybrid(self, query, paper_id, k=5) -> list[RetrievedChunk]
    def paths(self) -> dict[str, Callable]          # for the ablation
```

* One index pair per paper (BM25 over tokenised text; dense = embedding model
  + FAISS `IndexFlatIP` over unit vectors). Built lazily and cached in the
  retriever process, keyed by `(paper_id, chunker_version, paper.json mtime)`.
* Chunks longer than the encoder window are embedded as overlapping windows;
  a chunk scores as the max over its windows.
* Each arm contributes its top `max(4k, 20)` before Reciprocal Rank Fusion with
  `RRF_K = 60` (named `RRF_K`, never `k`). Fusion is over ranks, never scores.

### Prerequisite graph (`papermind/graph`)

```python
def normalize_concept(name: str) -> str                          # case, hyphens, plurals, whitespace
def find_gaps(target: str, known: set[str], prerequisites_of: Callable[[str], list[str]]) -> list[tuple[str, int]]
def teaching_order(gaps: list[str], prerequisites_of) -> list[str]  # prerequisites first (topological, BFS-depth ties)
def altitude_for(gap_count: int) -> Altitude                       # 0-2 technical, 3-10 scaffolded, >10 intuition

class GraphStore(Protocol):
    async def resolve(self, query: str) -> tuple[ConceptRef | None, float, list[ConceptRef]]
    async def subgraph(self, concept_id: str) -> dict[str, list[str]]   # id -> direct prerequisite ids, transitive closure
    async def known(self, user_id: str) -> dict[str, KnownConcept]
    async def mark(self, args: MarkKnownServerArgs) -> MarkKnownResult
    async def forget(self, user_id: str, concept_id: str) -> MarkKnownResult
    async def concept(self, concept_id: str) -> ConceptInfo | None
    async def search(self, q: str, limit: int) -> list[ConceptInfo]
    async def areas(self) -> list[ConceptArea]
    async def concepts(self, ids: list[str]) -> dict[str, ConceptInfo]   # batch lookup
    async def get_user(self, user_id) / upsert_user(profile)
    async def aclose(self) -> None
    backend: Literal["neo4j", "memory"]
class Neo4jGraphStore(GraphStore): ...
class InMemoryGraphStore(GraphStore): ...   # tests + no-Neo4j fallback, loads concepts.json
```

* BFS is written by hand over one fetched subgraph (one Cypher round trip,
  then a pure function): known concepts stop the walk; unknown ones are gaps and
  the walk continues into their prerequisites.
* Graph: `(:Concept {id,name,aliases,area,description})-[:REQUIRES]->(:Concept)`,
  `(:User {id,level,display_name,onboarded,created_at})-[:KNOWS {confidence,source,evidence,updated_at}]->(:Concept)`.
  A concept counts as known when `confidence >= settings.known_threshold`.
* Seed data: `data/concepts/concepts.json` (~350 concepts) built offline by a
  strong model, normalised, de-duplicated, and cycle-checked before insert.

### Semantic Scholar (`papermind/scholar`)

```python
class ScholarClient:
    async def lookup(self, query: str, limit: int = 3) -> list[ScholarPaper]
    async def references(self, paper: str, limit: int = 10) -> PaperGraphResult
    async def citations(self, paper: str, limit: int = 10) -> PaperGraphResult
```

A thin wrapper. The engineering is caching (on disk / Redis, TTL), a client-side
rate limiter, retry with exponential backoff + jitter on 429/5xx (honouring
Retry-After), timeouts, and reshaping into `ScholarPaper`. When it is down, the
tool returns an error result and the agent explains from the paper alone.

### MCP servers (`mcp_servers/*/server.py`)

MCP servers built on `mcp.server.mcpserver.MCPServer` (mcp 2.x; the 1.x
`FastMCP` import no longer exists), stdio by default, streamable HTTP at `/mcp`
when `--http --port N` is passed. Tools take flat, annotated parameters that
reuse the `*ServerArgs` field definitions (nested pydantic-model parameters
would wrap the arguments and `$ref` the schema). Result fields on the client are
snake_case (`structured_content`, `is_error`). `mcp_servers/prereq_graph/server.py`
is the reference implementation.
Each tool's parameters are exactly the `*ServerArgs` fields from
`papermind/schemas/tools.py`; each returns the matching `*Result` model as
structured content.

| server | tools |
|---|---|
| retriever | `search_paper(SearchPaperServerArgs) -> SearchPaperResult` |
| prereq_graph | `check_prerequisites`, `get_learning_path`, `mark_known`, `forget_concept` |
| semantic_scholar | `lookup_paper`, `get_references`, `get_citations` |

### MCP client (`papermind/mcp_client/manager.py`)

```python
class MCPManager:
    async def start(self) -> None            # spawn/connect all three, list tools
    async def call(self, server: str, tool: str, args: dict) -> dict   # structured result
    def tool_names(self, server: str) -> set[str]
    async def aclose(self) -> None
```

### Agent (`papermind/agent`)

**Registry** (`registry.py`) — the agent-visible tools, defined in code:

| model-facing tool | args model | routes to | injected context |
|---|---|---|---|
| `search_paper` | `SearchPaperArgs` | retriever.search_paper | `paper_id`, `mode` |
| `check_prerequisites` | `CheckPrerequisitesArgs` | prereq_graph.check_prerequisites | `user_id` |
| `get_learning_path` | `LearningPathArgs` | prereq_graph.get_learning_path | `user_id` |
| `lookup_paper` | `LookupPaperArgs` | semantic_scholar.lookup_paper | — |
| `get_references` | `PaperGraphArgs` | semantic_scholar.get_references | `default_paper` |
| `get_citations` | `PaperGraphArgs` | semantic_scholar.get_citations | `default_paper` |
| `compose_answer` | `ComposeAnswerArgs` | (local: ends the loop) | — |

The Anthropic tool list is built once at startup from
`ArgsModel.model_json_schema()`, **sorted by name and frozen** (tools render at
prefix position 0; a racy order would silently kill the cache). At startup the
registry checks each routed MCP tool exists. `mark_known` is deliberately not
agent-visible: mastery is written by the mastery pass, which can be undone.

**Dispatch** (`dispatch.py`) validates each `tool_use` input with its args
model. Invalid → `tool_result {is_error: true}` whose text is the readable
`ValidationError` (e.g. `k: Input should be a valid integer`); the model reads
it and retries. Unknown tool → error listing valid tools. After
`max_tool_retries` consecutive failures for a tool in a turn the dispatcher
answers "stop calling this tool" and the loop carries on. Valid calls run
concurrently with `asyncio.gather` when `parallel_tools` is on, sequentially
otherwise. All results for a step go back in **one** user message.

**Loop** (`loop.py`):

```python
class AgentRunner:
    async def run_turn(self, ctx: TurnContext, emit: Emit) -> TurnResult
Emit = Callable[[ChatEvent], Awaitable[None]]

@dataclass
class TurnContext:
    session: Session; user: UserProfile; paper: PaperMeta
    question: str; quote: Quote | None; level: Level
    known_summary: list[str]          # names of concepts the reader knows (for the agent)
```

1. Build messages: condensed history (prior user/assistant text, no tool
   chatter) + the new question (+ quoted selection) + a mid-conversation
   `{"role": "system"}` message carrying the volatile reader state (level,
   known concepts). Volatile state never goes in the top-level system prompt.
2. Repeat up to `max_agent_steps`: stream one model call → append the assistant
   content unchanged (thinking blocks included) → if it called
   `compose_answer` (valid) stop → else dispatch tools, emit `tool.*` /
   `gaps` events, append results, continue. A plain-text `end_turn` is treated
   as the plan. Running out of steps hands off with what was gathered.
3. Resolve sources (`compose_answer.source_chunk_ids`, falling back to the top
   retrieved chunks), label them S1…Sn, emit `sources`.
4. Stream the writer → `answer.delta` … `answer.done`.
5. Mastery pass → `knowledge.updated`. Persist the turn to the session. Emit
   `usage`, then `done`.

**Cache policy** (`cache_policy.py`): render order is tools → system →
messages; prefix matching stops at the first changed byte.

| breakpoint | content | changes |
|---|---|---|
| 1 | tools (sorted, frozen) + system block 1: `prompts/system.md` | never |
| 2 | system block 2: the paper card (title, authors, abstract, outline) | per paper |
| auto | top-level `cache_control` on the growing message tail | per step |

When `prompt_caching` is off, no `cache_control` is sent anywhere (that is the
ablation baseline). Usage `cache_read_input_tokens` is logged on every call;
zero across consecutive calls of a turn is a bug. Opening a session may
pre-warm breakpoints 1–2 with a `max_tokens: 0` request using the same
thinking/effort settings as real traffic.

**Writer** (`writer.py`):

```python
class Writer(Protocol):
    name: str
    def stream(self, inp: WriterInput) -> AsyncIterator[str]
class ClaudeWriter(Writer)       # prompts/writer.md level rubric, cites as [S1]
class MistralWriter(Writer)      # placeholder for the fine-tuned adapter; raises NotImplementedError
```

The writer sees the question, quote, level, altitude, plan, concepts to
scaffold (in teaching order), labelled sources, external papers, and the last
few turns. It must cite only the provided labels; unknown labels are stripped.

Altitude policy (the graph doesn't gate the answer, it selects the altitude):

| gaps | response |
|---|---|
| 0–2 | full explanation at the requested level; fill small gaps inline |
| 3–10 | teach the prerequisites in order, then the concept |
| > 10 | intuition-level explanation, plus a suggested learning path |

**Mastery** (`mastery.py`): after the answer, a low-effort structured-output
call reads the reader's message(s) and returns concepts they demonstrated and
concepts they revealed they don't know. A question like "wait, what's softmax?"
is strong negative evidence; "ok that makes sense" is at most weak positive.
Negative evidence is weighted more heavily than positive (agreement is a guess;
a question is ground truth). Updates go through `mark_known` / `forget_concept`
and are reported to the UI, which offers undo.

### LLM client (`papermind/llm/client.py`)

```python
class LLM:
    async def stream(self, *, system, messages, tools=None, effort, max_tokens,
                     cache: bool, on_text: Callable[[str], Awaitable[None]] | None = None) -> CallResult
    async def parse(self, *, system, messages, schema: type[BaseModel], effort, max_tokens) -> BaseModel
    async def prewarm(self, *, system, tools, effort) -> CallResult
@dataclass
class CallResult:
    message: Any; usage: Usage; latency_ms: int; ttft_ms: int | None; request_id: str | None
```

Async client on the aiohttp backend, model `claude-opus-5-5`, adaptive thinking
(always on for this model; depth set by `effort`), streaming for every call,
`eager_input_streaming` on client tools (our Pydantic layer is the validator),
server-side refusal fallback when `enable_fallbacks`, typed error mapping to
`ErrorCode`, and per-call cost from `pricing.py`. No sampling parameters
(rejected by this model family).

### Session state (`papermind/memory`)

```python
class SessionStore(Protocol):
    async def create(self, user_id, paper_id, level) -> Session
    async def get(self, session_id) -> Session | None
    async def save(self, session: Session) -> None       # refreshes the 24h TTL
    async def update(self, session_id, fn: Callable[[Session], Session]) -> Session  # per-session lock
class RedisSessionStore(SessionStore)      # session:{id}, JSON, EX ttl
class MemorySessionStore(SessionStore)     # tests only
```

Read-modify-write races (two overlapping requests on one session) are handled
with a short per-session Redis lock (`SET lock:session:{id} NX PX`).

### API (`papermind/api`)

Routes and bodies are listed in `papermind/schemas/api.py`. `POST /api/chat`
returns `text/event-stream` with the frames described in
`papermind/schemas/events.py`; it never returns a non-200 once streaming has
started — failures become an `error` event followed by `done`. Client
disconnects cancel the turn. Ingestion runs as a background task; its status
lives in Redis (`ingest:{paper_id}`) so any worker can report progress.

Errors before streaming use HTTP status + `{"error": {"code", "message"}}`.

## Evaluation

* Benchmark: `evals/benchmark/questions.jsonl`, 150 generated rows over 8 papers
  plus 24 natural-phrasing rows (3 per paper), each `{id, paper_id, question, target_level,
  gold_chunk, reference_answer, origin, query_type}`.
* Metrics: recall@5 (exact chunk-id match; no judge), faithfulness (claims
  checked against the retrieved chunks only, by a judge), token F1 vs the
  reference answer, calibration (blind judge labels the level), latency p50/p95
  per question, cost per query from usage.
* Runs: retrieval ablation (BM25 / dense / hybrid, recall@5 and @10) needs no
  API key; agent runs log to W&B (offline by default); latency ablation runs the
  same benchmark with `parallel_tools`+`prompt_caching` off vs on.
* pytest is the runner: `parametrize` over the benchmark, regression floors as
  asserts, `-m eval` to opt in to API-spending suites.
