"""Skeleton smoke test: the app imports and /health responds.

Exists so `pytest` is green from commit one and CI has something to run.
"""

from papermind.main import create_app


def test_create_app() -> None:
    app = create_app()
    assert app.title == "PaperMind"
