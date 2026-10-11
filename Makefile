.DEFAULT_GOAL := help
SHELL := /bin/bash

PY      := .venv/bin/python
PYTEST  := .venv/bin/pytest
UV      ?= uv
PORT    ?= 8000

.PHONY: help
help: ## List targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[1m%-16s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------- setup

.venv:
	$(UV) venv --python 3.11 .venv

.PHONY: install
install: .venv ## Python deps (runtime + dev) and frontend deps
	$(UV) pip install --python $(PY) -r requirements.txt -r requirements-dev.txt
	cd frontend && npm install

# ---------------------------------------------------------------- services

.PHONY: services
services: ## Start local Redis and Neo4j (Homebrew installs; use `make docker-services` otherwise)
	@redis-cli ping >/dev/null 2>&1 || redis-server --daemonize yes --port 6379 --save "" --appendonly no
	@neo4j status >/dev/null 2>&1 || neo4j start
	@echo "Redis: $$(redis-cli ping)"; neo4j status

.PHONY: stop-services
stop-services: ## Stop local Redis and Neo4j
	-redis-cli shutdown nosave
	-neo4j stop

.PHONY: docker-services
docker-services: ## Start Redis and Neo4j with Docker instead
	docker compose -f infra/docker-compose.yml up -d redis neo4j

.PHONY: seed
seed: ## Validate data/concepts and load the prerequisite graph into Neo4j
	$(PY) scripts/validate_concepts.py --strict
	$(PY) scripts/seed_graph.py

# ---------------------------------------------------------------- run

.PHONY: dev
dev: ## API on :8000 (spawns the three MCP servers over stdio)
	.venv/bin/uvicorn papermind.main:create_app --factory --reload --port $(PORT)

.PHONY: serve
serve: ## API with 4 workers, MCP servers over HTTP (start them with `make mcp-http`)
	MCP_TRANSPORT=http .venv/bin/uvicorn papermind.main:create_app --factory --workers 4 --port $(PORT)

.PHONY: mcp-http
mcp-http: ## Run the three MCP servers as standalone HTTP services (:8101-8103)
	$(PY) -m mcp_servers.retriever.server --http --port 8101 & \
	$(PY) -m mcp_servers.prereq_graph.server --http --port 8102 & \
	$(PY) -m mcp_servers.semantic_scholar.server --http --port 8103 & \
	wait

.PHONY: frontend
frontend: ## Vite dev server on :5173 (proxies /api to :8000)
	cd frontend && npm run dev

.PHONY: ask
ask: ## Ask a question from the terminal: make ask PAPER=1706.03762 LEVEL=beginner Q="How does attention work?"
	$(PY) scripts/ask.py --paper $(PAPER) --level $(or $(LEVEL),intermediate) "$(Q)"

# ---------------------------------------------------------------- data

.PHONY: ingest
ingest: ## Ingest one paper: make ingest ARXIV=1706.03762  (or FILE=paper.pdf)
	$(PY) -m papermind.ingest $(if $(ARXIV),--arxiv $(ARXIV),--file $(FILE))

.PHONY: ingest-benchmark
ingest-benchmark: ## Ingest the 8 pinned benchmark papers
	$(PY) scripts/ingest_benchmark.py

# ---------------------------------------------------------------- quality

.PHONY: test
test: ## Unit tests: no services, no network, no API key
	$(PYTEST)

.PHONY: test-int
test-int: ## Integration tests (Redis, Neo4j, network)
	$(PYTEST) -m integration tests/integration

.PHONY: lint
lint: ## Ruff + frontend lint
	.venv/bin/ruff check papermind mcp_servers evals scripts tests
	cd frontend && npm run lint

.PHONY: typecheck
typecheck: ## mypy + tsc
	.venv/bin/mypy papermind mcp_servers
	cd frontend && npm run typecheck

.PHONY: check
check: lint typecheck test ## What CI runs
	cd frontend && npm test -- --run && npm run build

# ---------------------------------------------------------------- evals

.PHONY: eval-retrieval
eval-retrieval: ## Recall@5/@10 ablation: BM25 vs dense vs hybrid (no API key needed)
	$(PY) -m evals.harness.run --suite retrieval --modes bm25,dense,hybrid --k 5,10

.PHONY: eval
eval: ## Full agent benchmark against the live API (spends money): faithfulness, F1, calibration, latency, cost
	$(PY) -m evals.harness.run --suite agent

LIMIT ?= 24

.PHONY: eval-latency
eval-latency: ## Latency A/B on LIMIT questions: sequential + no caching vs asyncio.gather + caching (spends money)
	$(PY) -m evals.harness.run --suite latency --config baseline --limit $(LIMIT)
	$(PY) -m evals.harness.run --suite latency --config optimized --limit $(LIMIT)
	$(PY) -m evals.harness.report --latest latency

.PHONY: eval-label-sheet
eval-label-sheet: ## Write a blind sheet of 30 answers to hand-label for judge agreement
	$(PY) -m evals.harness.run --suite agreement --prepare 30

.PHONY: eval-agreement
eval-agreement: ## Compare your hand labels with the calibration judge (Cohen's kappa)
	$(PY) -m evals.harness.run --suite agreement --labels evals/benchmark/hand_labels.csv

# ---------------------------------------------------------------- writer fine-tuning (see training/README.md)

MAX_COST ?= 5
PER_PAPER ?= 15

.PHONY: train-data-ingest
train-data-ingest: ## Fetch the 30 training papers (no API spend)
	$(PY) -m training.generate --ingest

.PHONY: train-data-pilot
train-data-pilot: ## 2 papers x 3 passages of training data, to check it (~$0.60)
	$(PY) -m training.generate --pilot

.PHONY: train-data
train-data: ## Generate training data (spends money; resumable): make train-data PER_PAPER=15 MAX_COST=45
	$(PY) -m training.generate --per-paper $(PER_PAPER) --max-cost $(MAX_COST)

.PHONY: train-filter
train-filter: ## Filter the generated rows (rules + Haiku level check)
	$(PY) -m training.filter --llm-flag

.PHONY: train-build
train-build: ## Build the SFT and DPO datasets
	$(PY) -m training.build --with-intermediate

WRITER ?= claude
MODEL ?=

.PHONY: eval-writer
eval-writer: ## Writer calibration with retrieval fixed: make eval-writer WRITER=mistral MODEL=papermind
	$(PY) -m evals.harness.run --suite writer --writer $(WRITER) $(if $(MODEL),--mistral-model $(MODEL),)
