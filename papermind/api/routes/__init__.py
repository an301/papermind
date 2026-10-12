"""Route modules, one per resource, mounted together under /api.

Everything but /health sits behind the access code when the server has one
(``papermind.api.guards.AccessGate``, a middleware): health stays open so the
frontend can learn that a code is needed.
"""

from fastapi import APIRouter

from papermind.api.routes import access, chat, concepts, feedback, health, papers, sessions, users

api_router = APIRouter(prefix="/api")
for module in (health, access, papers, users, concepts, sessions, chat, feedback):
    api_router.include_router(module.router)

__all__ = ["api_router"]
