# PaperMind

An AI research-paper reading agent. It reads ML papers alongside the user and
explains them at the user's actual expertise level — tracking which concepts
they already understand, detecting prerequisite gaps before explaining, pulling
the relevant sections of the paper, and pitching the explanation accordingly.

The three expertise levels (`beginner` / `intermediate` / `expert`) are not a
tone setting. They select prompt variants, change which prerequisites count as
"gaps", and form the primary slice of the eval harness.

---

## Hard constraints

These are project-defining. Do not relax them without the owner saying so.

1. **No agent frameworks.** No LangChain, no LlamaIndex, no SDK tool-runner
   helper. The ReAct loop is hand-written in `papermind/agent/loop.py` and
   stays readable top to bottom. The point of this project is understanding the
   control flow; a framework that hides the loop defeats it. If a library would
   own the loop, don't add it.
2. **Python 3.11, async throughout.** No sync I/O in request paths. The one
   unavoidable blocking call — local model generation — is quarantined behind a
   thread (see below), never awaited inline.
3. **Small, independently testable modules.** If a module can't be exercised
   without Redis, Neo4j, and a GPU, it's doing too much. Unit tests must run
   with no services and no weights.
4. **Comment the non-obvious choices.** Not what the code does — why this way.
   The existing stub docstrings are the standard to match.

---

## Architecture

Two models, split by job:

- **Agent reasoning → hosted Anthropic API (`claude-opus-5`).** Decides what to
  explain: which sections to retrieve, which prerequisites to check, when it has
  enough to answer.
- **Final explanation → locally-loaded fine-tuned Mistral 7B.** Decides how to
  say it, in our own explanation style at the requested level.

The split is intentional: reasoning quality needs a frontier model; voice
doesn't, and voice is what we fine-tuned.

### Request path

```
Browser (React/Vite)
   │  POST /api/chat            SSE back: typed events on one channel
   ▼
FastAPI  papermind/api/routes/chat.py
   │
   ▼
ReAct loop  papermind/agent/loop.py
   │   ├── Redis   → session: paper_id, scroll position, message history (24h TTL)
   │   ├── Neo4j   → concepts the user KNOWS, prerequisite traversal (persistent)
   │   └── MCP tools ─┬── retriever          (hybrid search over the active paper)
   │                  ├── prereq_graph       (concept lookup, missing prereqs, mark_known)
   │                  └── semantic_scholar   (citations, references, related work)
   ▼
Local Mistral 7B  papermind/llm/local_mistral.py  → streams the explanation
```

The loop is the standard three beats: **Reason** (hosted model call) → **Act**
(run requested tools concurrently) → **Observe** (append all tool results as one
user message) → repeat until `stop_reason != "tool_use"`.

### State boundary — Redis vs Neo4j

The rule, applied consistently:

> If losing it after 24 hours is acceptable, it's Redis. If it's something the
> user *learned*, it's Neo4j.

Scroll position, message history, active paper → Redis, 24h TTL, refreshed on
write. "This user understands backpropagation" → Neo4j, permanent. Both failure
modes are expensive: knowledge that evaporates overnight, or a graph slowly
filling with dead scroll offsets.

Neo4j shape:

```
(:Concept {id, name, aliases})
(:Concept)-[:REQUIRES]->(:Concept)                          // 350-concept DAG
(:User {id})-[:KNOWS {confidence, source, updated_at}]->(:Concept)
```

`KNOWS` edges carry confidence and provenance rather than being booleans —
mastery is inferred from conversation, not declared, and must be downgradable.
Users forget; a graph that only accumulates will confidently under-explain.

Prerequisite gaps are **one Cypher traversal** (`:REQUIRES` minus the user's
`:KNOWS`), not a Python graph walk. The graph is 350 nodes — optimize for query
clarity.

### MCP servers

Three stdio servers under `mcp_servers/`. They are separate processes, not
in-process functions, so the tool boundary is real and each server can be
exercised alone with the MCP inspector.

`prereq_graph.mark_known` is the only tool in the system that mutates
persistent user state. Keep it explicit, logged, and reversible.

`semantic_scholar` is the only server touching a third party, so it owns rate
limits, timeouts, and caching. The agent must degrade to explaining from the
paper alone when it's down.

---

## Working with the Anthropic API

Details that are easy to get wrong and expensive to get wrong:

- Model id is **`claude-opus-5`**, exactly. Never append a date suffix.
- Use `thinking={"type": "adaptive"}`. `budget_tokens` is **rejected with a
  400** on this model family — if you see it in code, it's stale.
- Tune cost and latency with `output_config={"effort": ...}`
  (`low`…`max`), not by downgrading the model.
- **Stream.** A ReAct turn over a full paper can exceed the non-streaming HTTP
  timeout, and we need the progress events regardless.
- Return **all** `tool_result` blocks for a turn in a **single** user message.
  Splitting them across messages trains the model to stop calling tools in
  parallel. Failed tools return `tool_result` with `is_error: true` — never a
  dropped block.
- `anthropic` 1.x runs on **`httpx2`**, not `httpx`. Passing an `httpx` object
  fails at request time. Install the `[aiohttp]` extra and use
  `DefaultAioHttpClient()`; the default backend is slower under concurrency.

### Prompt caching — the constraint that shapes the loop

Caching is a **prefix match**: one changed byte invalidates everything after
it. Render order is `tools` → `system` → `messages`. Max 4 breakpoints; minimum
cacheable prefix ~1024 tokens. Full placement rationale lives in
`papermind/agent/cache_policy.py`.

Two rules that constrain how the loop is written, both easy to violate by
accident:

1. **The tool list is built once at startup and sorted deterministically.**
   Tools render at prefix position 0. MCP servers return their tools in
   connection-completion order — which is *racy across restarts* — so an
   unsorted list silently produces a different prefix on every boot and a
   permanent 0% cache hit rate. `agent/tools/registry.py` sorts and freezes.
2. **Nothing volatile goes in the system prompt.** No dates, no session ids,
   and specifically **not the user's expertise level or known-concepts set** —
   those change the moment a `KNOWS` edge is written, which would invalidate
   every cached turn behind them. Inject them as a mid-conversation
   `{"role": "system", ...}` message appended to `messages[]` (supported on
   Opus 5); it sits *after* the cached history and leaves the prefix intact.

TTL: the 5-minute default fits an active reading session. `ttl: "1h"` doubles
write cost (2× vs 1.25×) and needs 3+ reads to break even — only for the
paper-content breakpoint, and only with measurements behind it.

**Verification:** `usage.cache_read_input_tokens`. If it's 0 across consecutive
turns of one session, something is mutating the prefix. That's a bug, not a
tuning problem.

---

## The async / local-model tension

The sharpest edge in the codebase, worth stating plainly: `transformers`
generation is blocking, compute-bound, and holds the GIL. Called from a
coroutine it stalls the whole event loop — every other user's SSE stream
freezes with it. In a codebase that is otherwise async throughout, this is the
one place the rule can't hold, so it's quarantined:

- Generation runs via `asyncio.to_thread` (or a dedicated process for real
  isolation) — never inline in a coroutine.
- A semaphore, normally sized **1**, guards it. There is one set of weights in
  VRAM; concurrent generation OOMs or thrashes. Queue instead.
- Weights load **lazily on first use**, not at startup, so `uvicorn --reload`
  stays usable. Cold load is tens of seconds and several GB.
- Tokens stream out via `TextIteratorStreamer` to keep the SSE channel live.
- With no accelerator, fall back to the hosted model **and record it in the
  trace**. A silent fallback makes eval numbers meaningless.

---

## Layout

```
papermind/
  main.py              app factory + lifespan (thin by design)
  config.py            all settings; nothing else reads os.environ
  api/routes/          health, session, chat (SSE)
  agent/
    loop.py            the ReAct loop — the heart of the project
    cache_policy.py    where the 4 cache breakpoints go and why
    trace.py           structured turn trace (SSE + tests + evals)
    tools/registry.py  MCP tools → frozen, sorted Anthropic tool list
    tools/dispatch.py  concurrent execution + tool_result formatting
    prompts/system.md  frozen system prompt — byte-stable
  llm/
    anthropic_client.py  hosted reasoning model
    local_mistral.py     local explanation model (see async tension above)
  memory/              Redis session state, 24h TTL
  graph/               Neo4j: concepts.py (prereqs), knows.py (user state)
  ingest/              arXiv -> section-aligned JSON chunks (built)
    arxiv.py           download, version resolution, PDF cache
    extract.py         PyMuPDF -> lines + typography (columns, hyphens)
    sections.py        heading detection -> real section structure
    chunker.py         paragraph packing, char-split backstop
    ids.py             stable chunk IDs  <- eval labels
    tokens.py          estimate, or exact via the API
    store.py           atomic JSON writes + manifest
  retrieval/           BM25 + dense + RRF over ingested chunks (built)
    text.py            indexable_text/tokenize -- shared by both arms
    bm25.py            lexical arm (rank_bm25, per paper)
    dense.py           embedding arm (MiniLM + FAISS, windowed)
    fusion.py          Reciprocal Rank Fusion -- pure, RRF_K = 60
    index.py           PaperIndex + IndexRegistry (in-memory cache)
    hybrid.py          the three ablation paths + search()
  schemas/             pydantic models; no I/O
mcp_servers/           three stdio MCP servers
frontend/              React + Vite, /api proxied to :8000 in dev
evals/                 150-question benchmark + W&B harness
infra/neo4j/           schema.cypher
tests/unit/            no services required — run on every commit
tests/integration/     needs docker compose; marked `integration`
```

---

## Ingestion

```bash
python -m papermind.ingest --arxiv-id 1706.03762
python -m papermind.ingest --from-file papers.txt      # batch
```

Output: `data/papers/{paper_id}.json` (metadata + chunks) and a `manifest.json`
index. Source PDFs are cached in `data/pdfs/` and gitignored; the chunk JSON is
tracked, so ID drift shows up in a diff.

### Chunk IDs are eval labels

`1706.03762:sec_3.2_attention`. Derived from paper id + the section's own number
+ its slugified title -- **never from position in a list**, so inserting a
section renumbers nothing. Split sections get `_p1`, `_p2`; unsplit ones get no
suffix. Repeated titles disambiguate by document order.

Three things can legitimately move an ID, all of them recorded in the output so
the break is visible rather than silent:

- **The arXiv version.** A bare id resolves to whatever is current today, and a
  new version can retitle a section. The resolved version is stored; pin it
  (`--arxiv-id 1706.03762v7`, or in `papers.txt`) once a paper is in the
  benchmark.
- **`CHUNKER_VERSION`** (`papermind/ingest/ids.py`). Bump it only when chunk
  boundaries or ID construction change, and treat a bump as a re-labelling event.
- **`--max-chunk-tokens`.** Changing it changes where sections split, and so
  changes part-suffixed IDs.

### Chunking strategy

Section-first, in three layers: a section that fits is one chunk; a section that
does not is packed by paragraph; a single paragraph over budget falls back to
character splitting at the nearest sentence boundary. On the Transformer paper
that yields 27 chunks, of which only References needs splitting.

### PDF extraction

The non-obvious parts, each of which silently corrupts chunks if skipped:

- **Two-column layouts** are detected per page and emitted in reading order,
  with full-width blocks splitting the page into bands. Naive extraction
  interleaves the columns into alternating half-sentences.
- **Whitespace-only spans must be joined, not filtered.** In many LaTeX PDFs
  the inter-word space is its own span; dropping blank spans glues words
  together (`vectors.The`). Typography measurements ignore blank spans; the
  text join does not.
- **Figure captions and in-figure labels** are moved to the end of their
  section rather than left in place -- in reading order they land *between* the
  two halves of a sentence.
- **Superscripts are kept.** Stripping them would tidy away footnote markers,
  but the same flag marks exponents, and that turns Section 4's O(n^2 * d)
  argument into O(n * d). A stray footnote digit is the cheaper error.
- **Running heads, page numbers, and the rotated arXiv margin stamp** are
  dropped; hyphenated line breaks are rejoined; ligatures are NFKC-folded so
  `classiﬁcation` and `classification` are one token to the retriever.

Known limitation: text drawn *inside* a figure (axis labels, diagram captions)
is not always separable from prose and can appear in a chunk. It is topically
relevant, so it degrades retrieval far less than a split sentence would.

### Token counts

`token_count` is a local estimate (~chars/4) by default -- chunking needs a
count for every candidate split, which rules out a network call. `--exact-tokens`
refines the stored counts via `messages.count_tokens` after chunking, and sets
`token_counter: "anthropic"`. Chunk boundaries are never recomputed from exact
counts: IDs must not depend on whether the flag was passed. No tiktoken -- it is
OpenAI's tokenizer and would be confidently wrong for a Claude budget.

---

## Retrieval

```python
chunks = await retriever.search(query, paper_id="1706.03762", k=5)   # hybrid
```

Three interchangeable paths, all `(query, paper_id, k=5) -> list[Chunk]`:

```python
await retriever.bm25_only(query, paper_id, k)
await retriever.dense_only(query, paper_id, k)
await retriever.hybrid(query, paper_id, k)
retriever.paths()          # {mode: callable} -- iterate this for the ablation
```

Identical signatures on purpose: an ablation should swap arms with no other
change, so the only variable is the strategy. `search_scored(...)` returns the
same results with `score`, `rank`, `bm25_rank` and `dense_rank` attached -- that
provenance is what turns "hybrid won" into "hybrid won *because* dense surfaced
what BM25 missed".

**`RRF_K` is not `k`.** `RRF_K = 60` is the RRF smoothing constant (Cormack et
al.); `k` is how many results you want. Conflating them is a silent bug, so the
constant is spelled `RRF_K` everywhere. Fusion is over *ranks*, never scores --
BM25 returns unbounded scores and the dense arm returns cosine similarities, and
any score-level blend would need a per-paper normalisation to tune.

Each arm contributes its top `max(4k, 20)` before fusion, not its top `k`.
Fusing only `k` throws away the recall that makes fusion worth doing: a chunk
ranked 7th by BM25 and 6th by dense should beat one ranked 1st by BM25 and
nowhere by dense.

### Windowing -- the encoder limit

all-MiniLM-L6-v2 caps at **256 tokens** and chunks run to 800. On the
Transformer paper **17 of 27 chunks exceed it**. Embedding chunks directly would
silently discard most of their text, and an ablation run on that would report
"dense retrieval is weak" when the finding is "dense retrieval never saw two
thirds of the corpus". So each chunk is split into overlapping encoder-sized
windows, every window is embedded, and a chunk scores as the **max** over its
windows -- max, not mean, because relevance is local. 27 chunks become 54
windows.

### Scoping and caching

One index pair per paper. A global index would be wrong twice over: BM25 would
compute IDF across every paper, and a global FAISS top-k would spend its budget
on chunks that get filtered away.

`IndexRegistry` caches indexes in memory for the process lifetime and builds
them at startup (`warm()`), with a per-paper lock so two concurrent
first-requests build once. A paper ingested after startup builds on demand. The
embedding model is loaded once and shared. Nothing is persisted to disk --
rebuilds are sub-second per paper once the model is up, and persisting would add
a cache-invalidation problem (chunks move when `CHUNKER_VERSION` does).

`IndexFlatIP` over unit-normalised vectors is exact cosine. At a few hundred
vectors per paper an approximate index would trade recall for a speedup nobody
needs.

### Measured behaviour

Post-warmup, on the Transformer paper: bm25 ~0.1ms, dense ~6ms, hybrid ~6ms.
Hybrid costs about what dense alone costs because the arms run concurrently.

Fusion earns its place on queries where the arms disagree. For *"why divide by
the square root of d_k?"* BM25 ranks `sec_5.3_optimizer` first (the optimizer
section is full of "rate", "square", "root" from the learning-rate schedule);
dense gets `sec_3.2.1` first; hybrid returns `sec_3.2.1`. For *"how much
computing power was needed to train it?"* -- no token shared with "Hardware and
Schedule" -- dense ranks it first and BM25 does not reach it in three.

**Known noise: the References section.** It is ingested as ordinary chunks, and
being a long low-specificity blob it surfaces for conceptual queries it cannot
answer (`sec_references_p3` reaches the hybrid top-5 for "what makes
self-attention efficient?"). Left as-is rather than filtered, because references
are legitimately retrievable for "who else worked on this?" -- but if benchmark
scores look oddly low, check for reference chunks in the results first. Worth an
ablation arm of its own.

---

## Commands

```bash
make venv install        # Python 3.11 venv + runtime and dev deps
cp .env.example .env     # then fill in ANTHROPIC_API_KEY
make up                  # Redis + Neo4j
make seed                # Neo4j constraints + concept graph
make dev                 # API on :8000
make frontend            # Vite on :5173, proxying /api

python -m papermind.ingest --arxiv-id 1706.03762   # ingest a paper

make test                # unit tests, no services and no network
make test-int            # integration tests (network; real arXiv fetch)
make eval                # 150-question benchmark → W&B
make check               # lint + typecheck + test (what CI runs)
```

`make install-model` pulls the torch/transformers stack — several GB, GPU
machines only. It is deliberately **not** in `requirements.txt` so CI, unit
tests, and frontend work stay light.

---

## Conventions

- Settings only via `app/config.py`. No module reads `os.environ` directly, so
  every module imports cleanly in a bare test environment.
- Tests are marked `integration`, `gpu`, or `eval`; the default `pytest` run
  excludes `integration`. Keep the default run fast and serviceless.
- `pytest-asyncio` is in `asyncio_mode = "auto"` — no per-test decorators.
- Nothing under `papermind/` imports from `scripts/`.
- Eval results are sliced **by expertise level**, not just aggregate. A model
  that nails expert questions and patronises beginners scores fine overall and
  is useless.

---

## Open decisions

Not yet made. Don't quietly pick one — raise it.

- **Vector store and fusion strategy** for `retrieval/hybrid.py`. Hybrid rather
  than pure dense because ML papers are full of exact tokens embeddings blur —
  notation, method names, datasets. Someone asking about "LoRA" wants the
  section that says LoRA.
- **Where the 350-concept ontology comes from** — hand-authored, derived from a
  curriculum, or extracted from papers.
- **How `KNOWS` confidence is inferred** from conversation, and what evidence
  downgrades an edge.

---

## Environment notes

- The project targets **Python 3.11**; the system python here is 3.13. Use
  `make venv` (which calls `python3.11`) rather than the default interpreter —
  the pins in `requirements-model.txt` are the ones most likely to bite on 3.13.
- **Docker is not currently installed on this machine.** `make up` will fail
  until Docker Desktop (or Colima) is available.

---

## Status

Skeleton only — directories, configuration, and stub modules carrying design
intent. `make test` is green (`create_app()` returns an app with a `/health`
liveness route; nothing else is wired). No feature code has been written yet,
by request.
