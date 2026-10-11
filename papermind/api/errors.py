"""HTTP errors as ``{"error": {"code", "message"}}``.

Every non-2xx response the API produces has this shape, including FastAPI's
own validation failures and unhandled exceptions, so the frontend has exactly
one error format to parse. Messages are written for the reader, not the
developer: what went wrong and what to do about it.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from papermind.memory.session_store import SessionBusy, SessionNotFound
from papermind.schemas.api import AddPaperError

log = logging.getLogger(__name__)


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorBody(BaseModel):
    error: ErrorDetail


class AddPaperErrorBody(BaseModel):
    """What the add-paper routes document: the same envelope, with the add-paper fields."""

    error: AddPaperError


class APIError(Exception):
    """Raise from routes and dependencies; rendered as the standard error body.

    ``extra`` adds fields beside ``code`` and ``message`` (the add-paper errors
    carry ``latest_version`` and ``paper_id``); ``None`` values are left out.
    """

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        headers: dict[str, str] | None = None,
        extra: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = headers
        self.extra = extra or {}


def add_paper_error(status_code: int, error: AddPaperError) -> APIError:
    extra = error.model_dump(exclude={"code", "message"}, exclude_none=True)
    return APIError(status_code, error.code, error.message, extra=extra)


SESSION_NOT_FOUND_MESSAGE = (
    "This reading session has expired or does not exist. Reopen the paper to start a new one."
)
SESSION_BUSY_MESSAGE = "This session is being updated by another request. Try again in a moment."


def not_found(code: str, message: str) -> APIError:
    return APIError(404, code, message)


def conflict(code: str, message: str) -> APIError:
    return APIError(409, code, message)


def error_response(
    status_code: int,
    code: str,
    message: str,
    headers: dict[str, str] | None = None,
    extra: dict[str, Any] | None = None,
) -> JSONResponse:
    detail = {**(extra or {}), **ErrorDetail(code=code, message=message).model_dump()}
    return JSONResponse({"error": detail}, status_code=status_code, headers=headers)


# Shared OpenAPI declaration so error responses are documented on each route.
def error_responses(
    *status_codes: int, model: type[BaseModel] = ErrorBody
) -> dict[int | str, dict[str, Any]]:
    return {code: {"model": model} for code in status_codes}


_STATUS_CODES: dict[int, str] = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "validation_error",
    429: "rate_limited",
    503: "unavailable",
}

_STATUS_MESSAGES: dict[int, str] = {
    404: "There is nothing at this address.",
    405: "This endpoint does not accept that HTTP method.",
}


def describe_validation_errors(errors: Sequence[Any]) -> str:
    """One readable line per problem, e.g. ``body.message: String should have at least 1 character``."""
    lines: list[str] = []
    for err in errors:
        loc = [str(part) for part in err.get("loc", ()) if part not in ("body", "query", "path")]
        where = ".".join(loc) if loc else None
        msg = err.get("msg", "is invalid")
        if err.get("type") == "missing":
            msg = "is required"
        elif err.get("type") == "json_invalid":
            where, msg = None, "The request body is not valid JSON."
        lines.append(f"{where}: {msg}" if where else msg)
    return "; ".join(dict.fromkeys(lines)) or "The request is invalid."


async def _api_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, APIError)
    return error_response(exc.status_code, exc.code, exc.message, exc.headers, exc.extra)


async def _validation_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    return error_response(422, "validation_error", describe_validation_errors(exc.errors()))


async def _http_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    code = _STATUS_CODES.get(exc.status_code, "http_error")
    detail = exc.detail if isinstance(exc.detail, str) else None
    # Starlette's default details ("Not Found") read as jargon in the UI.
    message = _STATUS_MESSAGES.get(exc.status_code) or detail or "The request could not be completed."
    headers = getattr(exc, "headers", None)
    return error_response(exc.status_code, code, message, headers)


async def _session_not_found(_: Request, exc: Exception) -> JSONResponse:
    return error_response(404, "session_not_found", SESSION_NOT_FOUND_MESSAGE)


async def _session_busy(_: Request, exc: Exception) -> JSONResponse:
    return error_response(409, "session_busy", SESSION_BUSY_MESSAGE, {"Retry-After": "1"})


INTERNAL_ERROR_MESSAGE = "Something went wrong on the server. Try again in a moment."


def internal_error_response() -> JSONResponse:
    return error_response(500, "internal", INTERNAL_ERROR_MESSAGE)


async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    # Only reached for failures outside the middleware stack's
    # CatchUnhandled (in the middleware itself); see papermind.api.middleware.
    log.error("unhandled error on %s %s", request.method, request.url.path, exc_info=exc)
    return internal_error_response()


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(APIError, _api_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(SessionNotFound, _session_not_found)
    app.add_exception_handler(SessionBusy, _session_busy)
    app.add_exception_handler(Exception, _unhandled)
