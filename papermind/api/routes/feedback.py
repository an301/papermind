"""POST /api/feedback: the reader reports a problem with an answer."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Response

from papermind.api.deps import ResourcesDep
from papermind.api.errors import error_responses
from papermind.memory.feedback import append_feedback, build_record, feedback_path
from papermind.schemas.api import FeedbackRequest
from papermind.schemas.session import Session

log = logging.getLogger(__name__)

router = APIRouter(tags=["feedback"])


@router.post("/feedback", status_code=204, responses=error_responses(422))
async def send_feedback(body: FeedbackRequest, res: ResourcesDep) -> Response:
    """Append the report to ``{data_dir}/feedback/feedback.jsonl``.

    Accepted whether or not the session still exists: a report about an
    answer from yesterday is as useful as one from a minute ago, it just
    arrives without the answer's text.
    """
    session: Session | None = None
    try:
        session = await res.sessions.get(body.session_id)
    except Exception:
        # The report itself is what matters; the session only adds context.
        log.warning("could not read session %s for a feedback report", body.session_id, exc_info=True)
    record = build_record(body, session)
    await append_feedback(feedback_path(res.settings.data_dir), record)
    log.info("feedback on %s: %s", body.message_id, body.reason)
    return Response(status_code=204)
