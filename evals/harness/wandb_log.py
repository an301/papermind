"""Weights & Biases logging for eval runs: config, summary metrics, and a per-question table.

Offline by default (``settings.wandb_mode``): runs land under ``wandb/`` and
can be pushed later with ``wandb sync``. Logging is a side channel and never
fails a run: with mode "disabled", wandb missing, or ``wandb.init`` raising,
every method is a no-op and the result JSON in evals/results/ is still
written.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from papermind.config import REPO_ROOT, Settings

log = logging.getLogger(__name__)


def settings_snapshot(settings: Settings) -> dict[str, Any]:
    """Every setting except secrets, JSON-safe: the switches a run was made with."""
    out: dict[str, Any] = {}
    for name in type(settings).model_fields:
        value = getattr(settings, name)
        if isinstance(value, SecretStr) or name.endswith(("_api_key", "_password")):
            continue
        out[name] = str(value) if isinstance(value, Path) else value
    return out


def flatten(metrics: Mapping[str, Any], prefix: str = "") -> dict[str, float | int]:
    """Nested summary -> {"overall/hybrid/recall@5": 0.91}; numeric leaves only."""
    flat: dict[str, float | int] = {}
    for key, value in metrics.items():
        name = f"{prefix}/{key}" if prefix else str(key)
        if isinstance(value, Mapping):
            flat.update(flatten(value, name))
        elif isinstance(value, bool):
            flat[name] = int(value)
        elif isinstance(value, int | float):
            flat[name] = value
    return flat


def _cell(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return json.dumps(value, default=str)


class WandbLogger:
    def __init__(
        self,
        suite: str,
        *,
        settings: Settings,
        config: Mapping[str, Any],
        name: str | None = None,
        enabled: bool = True,
    ) -> None:
        self.run: Any = None
        if not enabled or settings.wandb_mode == "disabled":
            return
        try:
            import wandb

            self.run = wandb.init(
                project=settings.wandb_project,
                mode=settings.wandb_mode,
                job_type=suite,
                name=name,
                config=dict(config),
                dir=str(REPO_ROOT),
                settings=wandb.Settings(silent=True, x_disable_stats=True),
            )
        except Exception:
            log.warning("W&B logging disabled for this run: wandb.init failed", exc_info=True)
            self.run = None

    @property
    def enabled(self) -> bool:
        return self.run is not None

    def log_summary(self, summary: Mapping[str, Any]) -> None:
        if self.run is None:
            return
        try:
            flat = flatten(summary)
            self.run.log(flat)
            self.run.summary.update(flat)
        except Exception:
            log.warning("W&B summary logging failed", exc_info=True)

    def log_table(
        self, key: str, rows: Sequence[Mapping[str, Any]], columns: Sequence[str] | None = None
    ) -> None:
        if self.run is None or not rows:
            return
        try:
            import wandb

            cols: list[Any] = list(columns) if columns is not None else list(rows[0])
            table = wandb.Table(columns=cols, data=[[_cell(r.get(c)) for c in cols] for r in rows])
            self.run.log({key: table})
        except Exception:
            log.warning("W&B table logging failed", exc_info=True)

    def finish(self) -> str | None:
        """Close the run; returns where it was written (offline dir or URL), if anywhere."""
        if self.run is None:
            return None
        where = getattr(self.run, "url", None) or getattr(self.run, "dir", None)
        try:
            self.run.finish()
        except Exception:
            log.warning("W&B finish failed", exc_info=True)
        self.run = None
        return where

    def __enter__(self) -> WandbLogger:
        return self

    def __exit__(self, *exc: object) -> None:
        self.finish()
