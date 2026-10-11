"""Semantic Scholar: cached, rate-limited lookups of papers, references, and citations."""

from papermind.scholar.cache import Cache, FileCache, NullCache, RedisCache, open_cache
from papermind.scholar.client import ScholarAPI, ScholarClient, ScholarStats
from papermind.scholar.errors import ScholarError, ScholarNotFound, ScholarRateLimited, ScholarUnavailable

__all__ = [
    "Cache",
    "FileCache",
    "NullCache",
    "RedisCache",
    "ScholarAPI",
    "ScholarClient",
    "ScholarError",
    "ScholarNotFound",
    "ScholarRateLimited",
    "ScholarStats",
    "ScholarUnavailable",
    "open_cache",
]
