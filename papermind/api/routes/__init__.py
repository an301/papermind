"""Route modules, one per resource, mounted together under /api.

Everything but /health sits behind the access code when the server has one:
health stays open so the frontend can learn that a code is needed.
"""

from fastapi import APIRouter, Depends

from papermind.api.guards import require_access
from papermind.api.routes import access, chat, concepts, feedback, health, papers, sessions, users

api_router = APIRouter(prefix="/api")
api_router.include_router(health.router)
for module in (access, papers, users, concepts, sessions, chat, feedback):
    api_router.include_router(module.router, dependencies=[Depends(require_access)])

__all__ = ["api_router"]
