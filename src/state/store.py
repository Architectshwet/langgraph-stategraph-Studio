from typing import Any, Optional
import os

from langgraph.store.redis.aio import AsyncRedisStore
from redis.asyncio import Redis

from src.utils.logger import get_logger
from src.db.redis_client import get_redis_client, get_redis_url

logger = get_logger(__name__)

_store: AsyncRedisStore | None = None


def get_store() -> AsyncRedisStore:
    """Get or create the global AsyncRedisStore instance."""
    global _store
    if _store is None:
        redis_url = get_redis_url()
        # AsyncRedisStore expects a URL string for proper initialization via RedisVL
        _store = AsyncRedisStore(redis_url)
        logger.info("AsyncRedisStore initialized for persistent session data")
    return _store


async def get_session(store: AsyncRedisStore, thread_id: str) -> dict[str, Any]:
    """Get the entire session data for a thread from Redis."""
    namespace = ("session", thread_id)
    items = await store.asearch(namespace, limit=100)

    session: dict[str, Any] = {}
    for item in items:
        if item.key and item.value is not None:
            session[item.key] = item.value

    return session


async def set_session(store: AsyncRedisStore, thread_id: str, session: dict[str, Any]) -> None:
    """Set the entire session data for a thread in Redis."""
    namespace = ("session", thread_id)

    for key, value in session.items():
        # Set a 24-hour TTL for session data to ensure automatic cleanup
        await store.aput(namespace=namespace, key=key, value=value, ttl=86400)

        try:
            from src.db.postgres_session_sync import postgres_session_sync_service

            if postgres_session_sync_service.is_sync_enabled:
                await postgres_session_sync_service.sync_session_field(
                    namespace=namespace,
                    key=key,
                    value=value if isinstance(value, dict) else {"value": value},
                    thread_id=thread_id,
                )
        except Exception as exc:
            logger.warning("Failed to sync session data to PostgreSQL: %s", exc)


def reset_store():
    global _store
    _store = None
    logger.info("Store reset")


def cleanup_store():
    global _store
    _store = None
    logger.info("Store cleanup")
