"""All settings. Nothing else in the codebase reads os.environ.

Keeping configuration in one place means every module imports cleanly in a
bare test environment, and the eval harness can flip a single setting (prompt
caching, parallel tool dispatch, retrieval mode) and know nothing else moved.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent

Effort = Literal["low", "medium", "high", "xhigh", "max"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- app
    app_version: str = "0.1.0"
    environment: Literal["dev", "test", "prod"] = "dev"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"])
    data_dir: Path = REPO_ROOT / "data"
    log_level: str = "INFO"

    # ---- Anthropic
    anthropic_api_key: SecretStr | None = None
    model: str = "claude-opus-5-5"
    # Agent routing decisions are simple (which tool next?) -- low effort keeps
    # the three or four sequential calls per question fast. The writer also runs
    # at low: measured on the Transformer paper, medium spent ~7 s thinking
    # before the first visible word (time to first token 15.7 s vs 8.7 s) for
    # answers a reader couldn't tell apart. Raise it per deployment if needed.
    agent_effort: Effort = "low"
    writer_effort: Effort = "low"
    classifier_effort: Effort = "low"
    judge_effort: Effort = "low"
    agent_max_tokens: int = 16000
    writer_max_tokens: int = 16000
    # Server-side refusal fallback (beta). Routes a declined request to a
    # fallback model inside the same call instead of failing the turn.
    enable_fallbacks: bool = True

    # ---- agent loop
    max_agent_steps: int = 6
    max_tool_retries: int = 2
    # The two latency optimisations, as switches so the eval harness can run
    # the same benchmark with each off and on.
    parallel_tools: bool = True
    prompt_caching: bool = True
    prewarm_cache: bool = True
    writer_backend: Literal["claude", "mistral"] = "claude"
    # The fine-tuned writer, served by vLLM's OpenAI-compatible API, e.g.
    #   vllm serve mistralai/Mistral-7B-Instruct-v0.3 --enable-lora \
    #     --lora-modules papermind=<hf-repo-or-path> --max-lora-rank 16 --port 8001
    # mistral_model is the LoRA module name ("papermind"), or the base model id
    # to run the untuned baseline through the same server.
    mistral_base_url: str = "http://localhost:8001/v1"
    mistral_model: str = "papermind"
    mistral_api_key: SecretStr | None = None
    mistral_max_tokens: int = 1200
    mistral_temperature: float = 0.3
    mistral_timeout_s: float = 120.0
    history_turns: int = 6

    # ---- retrieval
    embedding_model: str = "BAAI/bge-base-en-v1.5"
    retrieval_k: int = 5
    retrieval_mode: Literal["hybrid", "bm25", "dense"] = "hybrid"
    rrf_k: int = 60
    max_chunk_tokens: int = 600

    # ---- MCP servers
    mcp_transport: Literal["stdio", "http"] = "stdio"
    mcp_retriever_url: str = "http://127.0.0.1:8101/mcp"
    mcp_prereq_url: str = "http://127.0.0.1:8102/mcp"
    mcp_scholar_url: str = "http://127.0.0.1:8103/mcp"
    mcp_call_timeout_s: float = 30.0

    # ---- Redis (session state)
    redis_url: str = "redis://localhost:6379/0"
    session_ttl_seconds: int = 24 * 60 * 60

    # ---- Neo4j (concept graph + mastery)
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: SecretStr = SecretStr("papermind-dev")
    known_threshold: float = 0.6

    # ---- Semantic Scholar
    s2_api_key: SecretStr | None = None
    s2_base_url: str = "https://api.semanticscholar.org/graph/v1"
    s2_cache_ttl_seconds: int = 7 * 24 * 60 * 60

    # ---- arXiv
    arxiv_user_agent: str = "PaperMind/0.1 (research reading assistant)"

    # ---- uploads
    max_upload_mb: int = 40

    # ---- public deployment guards. All off by default so local dev is
    # unchanged; a public server turns them on in its .env (DEPLOY.md).
    # Every question spends the operator's Anthropic credit, so a public
    # server needs a gate and a ceiling, not just good intentions.
    access_code: SecretStr | None = None
    questions_per_hour: int = 0  # per client IP; 0 = unlimited
    papers_per_day: int = 0  # arXiv adds + uploads per client IP; 0 = unlimited
    # Model spend across every reader, per UTC day. Once reached, chat answers
    # with a "come back tomorrow" error and suggestions fall back to section
    # titles. 0 = no cap. The Anthropic Console's own limit is the backstop.
    daily_budget_usd: float = 0.0
    # The library is shared by everyone on the server, so one visitor could
    # remove everyone's papers.
    allow_paper_delete: bool = True

    # ---- evals
    wandb_project: str = "papermind"
    wandb_mode: Literal["online", "offline", "disabled"] = "offline"

    @property
    def papers_dir(self) -> Path:
        return self.data_dir / "papers"

    @property
    def concepts_path(self) -> Path:
        return self.data_dir / "concepts" / "concepts.json"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def has_api_key(self) -> bool:
        return self.anthropic_api_key is not None and bool(self.anthropic_api_key.get_secret_value().strip())

    @property
    def access_code_required(self) -> bool:
        return self.access_code is not None and bool(self.access_code.get_secret_value().strip())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
