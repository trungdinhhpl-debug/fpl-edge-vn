"""Small cache abstraction.

Uses Redis when REDIS_URL is set, otherwise an in-process TTL dict.
Only JSON-serialisable values should be stored.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any

from app.config import settings


class _InMemoryTTLCache:
    def __init__(self) -> None:
        self._store: dict[str, tuple[float, str]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        with self._lock:
            item = self._store.get(key)
            if not item:
                return None
            expires_at, payload = item
            if expires_at < time.time():
                self._store.pop(key, None)
                return None
            return json.loads(payload)

    def set(self, key: str, value: Any, ttl: int) -> None:
        with self._lock:
            self._store[key] = (time.time() + ttl, json.dumps(value, default=str))

    def delete(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def clear_prefix(self, prefix: str) -> None:
        with self._lock:
            for k in [k for k in self._store if k.startswith(prefix)]:
                self._store.pop(k, None)


class _RedisCache:  # pragma: no cover - exercised only when Redis configured
    def __init__(self, url: str) -> None:
        import redis  # local import so redis isn't required in dev

        self._r = redis.Redis.from_url(url, decode_responses=True)

    def get(self, key: str) -> Any | None:
        raw = self._r.get(key)
        return json.loads(raw) if raw else None

    def set(self, key: str, value: Any, ttl: int) -> None:
        self._r.set(key, json.dumps(value, default=str), ex=ttl)

    def delete(self, key: str) -> None:
        self._r.delete(key)

    def clear_prefix(self, prefix: str) -> None:
        for k in self._r.scan_iter(match=f"{prefix}*"):
            self._r.delete(k)


def _build_cache():
    if settings.redis_url:
        try:
            return _RedisCache(settings.redis_url)
        except Exception:  # fall back gracefully
            return _InMemoryTTLCache()
    return _InMemoryTTLCache()


cache = _build_cache()


def cached_json(key: str, ttl: int | None = None):
    """Decorator caching a function's JSON-serialisable return value."""
    ttl = ttl or settings.cache_ttl_seconds

    def wrapper(fn):
        def inner(*args, **kwargs):
            hit = cache.get(key)
            if hit is not None:
                return hit
            value = fn(*args, **kwargs)
            cache.set(key, value, ttl)
            return value

        return inner

    return wrapper
