"""Typed settings, loaded once from the environment.

Everything configurable lives here so no other module reads `os.environ` --
which is what keeps modules importable in tests without a populated environment.

See `.env.example` for the full variable list and what each one is for.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",   # .env carries vars for the MCP servers too
    )

    # --- hosted model (agent reasoning) ---
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5"
    anthropic_effort: str = "high"
    anthropic_max_tokens: int = 16000

    # --- retrieval ---
    papers_dir: Path = Path("data/papers")
    embedding_model: str = "all-MiniLM-L6-v2"
    # RRF smoothing constant -- not the number of results. See fusion.py.
    rrf_k: int = 60
    retrieval_top_k: int = 5
    # Build every ingested paper's index during startup. Turning this off makes
    # the first request for a paper pay the build cost instead.
    warm_indexes_at_startup: bool = True

    # --- app ---
    log_level: str = "INFO"
    frontend_origin: str = "http://localhost:5173"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached so the .env file is read once per process."""
    return Settings()
