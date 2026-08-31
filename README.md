# PaperMind

An AI research-paper reading agent. It reads ML papers with you and explains
them at your level — tracking which concepts you already understand, spotting
prerequisite gaps before it explains, and pulling the sections of the paper that
actually answer your question.

## How it works

A hand-written ReAct loop (no LangChain, no LlamaIndex) reasons on the Anthropic
API and hands the final explanation to a locally-loaded fine-tuned Mistral 7B.
Session state lives in Redis; what you know lives in a Neo4j concept graph with
350 prerequisite-linked concepts. Retrieval, graph queries, and Semantic Scholar
lookups are exposed to the agent as three MCP servers.

```
React + Vite  →  FastAPI  →  ReAct loop  →  Anthropic (reason)
                                 │            Mistral 7B, local (explain)
                                 ├── Redis   session state, 24h TTL
                                 ├── Neo4j   concept graph + what you know
                                 └── MCP     retriever · prereq graph · Semantic Scholar
```

## Quick start

Requires Python 3.11, Node 20+, and Docker.

```bash
make venv install        # venv + Python dependencies
cp .env.example .env     # add your ANTHROPIC_API_KEY
make up                  # Redis + Neo4j
make seed                # load the concept graph
make dev                 # API on http://localhost:8000
make frontend            # UI on http://localhost:5173
```

Ingest a paper:

```bash
python -m papermind.ingest --arxiv-id 1706.03762
# 1706.03762v7  Attention Is All You Need
#   15 pages -> 27 chunks (3 from split sections), ~9,360 tokens
```

Chunks land in `data/papers/1706.03762.json`, keyed by stable, readable IDs like
`1706.03762:sec_3.2_attention` that map onto the paper's own section numbering.

To run the local explanation model, add `make install-model` on a machine with
a GPU (or enough unified memory) and set `LOCAL_MODEL_PATH` in `.env`. Without
it, set `LOCAL_MODEL_FALLBACK_TO_HOSTED=true` and explanations come from the
hosted model instead.

Search it:

```python
from papermind.retrieval import HybridRetriever, IndexRegistry

registry  = IndexRegistry(Path("data/papers"))
retriever = HybridRetriever(registry)
await registry.warm()

await retriever.search("why divide by the square root of d_k?", "1706.03762", k=5)
```

BM25 and dense embeddings merged with Reciprocal Rank Fusion, scoped to the
paper being read. `bm25_only`, `dense_only`, and `hybrid` share one signature so
they can be swapped in an ablation.

## Development

```bash
make test        # unit tests — no services needed
make test-int    # integration tests — needs `make up`
make eval        # 150-question benchmark, logged to Weights & Biases
make check       # lint + typecheck + test
```

## Status

Ingestion and retrieval work end to end. The agent loop, model clients, stores,
MCP servers, and frontend are still skeleton.

Architecture and the reasoning behind it: [`CLAUDE.md`](./CLAUDE.md).
