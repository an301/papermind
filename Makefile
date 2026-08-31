# PaperMind developer commands.
.PHONY: help venv install install-model up down logs seed dev frontend test test-int eval lint fmt typecheck check

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "};{printf "  \033[36m%-14s\033[0m %s\n",$$1,$$2}'

venv:  ## Create a Python 3.11 venv (project targets 3.11, not the system python)
	python3.11 -m venv .venv

install:  ## Install runtime + dev dependencies
	.venv/bin/pip install -r requirements.txt -r requirements-dev.txt

install-model:  ## Install the local Mistral stack (GPU machines only, several GB)
	.venv/bin/pip install -r requirements-model.txt

up:  ## Start Redis and Neo4j
	docker compose up -d

down:  ## Stop services (data preserved)
	docker compose down

logs:  ## Tail service logs
	docker compose logs -f

seed:  ## Apply Neo4j constraints and load the concept graph
	.venv/bin/python scripts/init_neo4j.py

dev:  ## Run the API with reload
	.venv/bin/uvicorn papermind.main:app --reload --port 8000

frontend:  ## Run the Vite dev server
	cd frontend && npm run dev

test:  ## Unit tests (no services required)
	.venv/bin/pytest

test-int:  ## Integration tests (requires `make up`)
	.venv/bin/pytest -m integration

eval:  ## Run the 150-question benchmark and log to W&B
	.venv/bin/python -m evals.harness.run

lint:  ## Lint
	.venv/bin/ruff check .

fmt:  ## Format
	.venv/bin/ruff format .

typecheck:  ## Type-check
	.venv/bin/mypy papermind mcp_servers

check: lint typecheck test  ## Everything CI runs
