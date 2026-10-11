"""Route modules, one per resource, mounted together under /api."""

from fastapi import APIRouter

from papermind.api.routes import chat, concepts, feedback, health, papers, sessions, users

api_router = APIRouter(prefix="/api")
for module in (health, papers, users, concepts, sessions, chat, feedback):
    api_router.include_router(module.router)

__all__ = ["api_router"]
