"""The HTTP surface: routes, middleware and error handlers, with no I/O of its own.

``papermind.main.create_app`` adds the lifespan that opens the real resources;
tests build the same app around fakes with ``build_app(settings, resources=...)``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from papermind.api.deps import Resources
from papermind.api.errors import install_error_handlers
from papermind.api.middleware import CatchUnhandled, RequestSizeLimit
from papermind.api.routes import api_router
from papermind.config import Settings

Lifespan = Callable[[FastAPI], AbstractAsyncContextManager[None]]

# pdf.js reads these to decide whether it can fetch a PDF in ranges when the
# API is on another origin; without them it silently downloads the whole file.
_EXPOSED_HEADERS = ["Accept-Ranges", "Content-Range", "Content-Length", "Content-Disposition"]


def build_app(
    settings: Settings, *, lifespan: Lifespan | None = None, resources: Resources | None = None
) -> FastAPI:
    if resources is not None:
        injected = resources

        @asynccontextmanager
        async def lifespan(app: FastAPI) -> AsyncIterator[None]:
            app.state.resources = injected
            yield

    app = FastAPI(
        title="PaperMind",
        version=settings.app_version,
        summary="Read a paper; ask questions; get answers at your level, grounded in the paper.",
        lifespan=lifespan,
    )
    if resources is not None:
        # Also set eagerly: an ASGI test transport does not run the lifespan.
        app.state.resources = resources
    install_error_handlers(app)
    # Each add_middleware wraps everything added before it, so CORS goes last:
    # outermost, it puts its headers on every response the others produce,
    # the 413s and the catch-all 500 included.
    app.add_middleware(
        RequestSizeLimit, upload_path="/api/papers/upload", max_upload_mb=settings.max_upload_mb
    )
    app.add_middleware(CatchUnhandled)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=_EXPOSED_HEADERS,
    )
    app.include_router(api_router)
    return app
