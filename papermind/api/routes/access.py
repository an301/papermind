"""GET /api/access: does this request carry a working access code?

The frontend asks this once after the reader types a code, so a typo is
caught on the gate screen instead of as a failed request later. The check
itself is the router-level ``require_access`` dependency.
"""

from __future__ import annotations

from fastapi import APIRouter, Response

from papermind.api.errors import error_responses

router = APIRouter(tags=["access"])


@router.get("/access", status_code=204, responses=error_responses(401, 429))
async def check_access() -> Response:
    """204 when the code is right, or when this server needs none."""
    return Response(status_code=204)
