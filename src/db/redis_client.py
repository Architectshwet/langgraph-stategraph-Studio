import os
from redis.asyncio import Redis, ConnectionPool
from src.utils.logger import get_logger

logger = get_logger(__name__)

_redis_client: Redis | None = None
_pool: ConnectionPool | None = None

def get_redis_url() -> str:
    """Construct the Redis connection URL from environment variables."""
    host = os.getenv("REDIS_HOST", "localhost")
    port = os.getenv("REDIS_PORT", "6379")
    username = os.getenv("REDIS_USERNAME", "default")
    password = os.getenv("REDIS_PASSWORD", "")
    
    if password:
        # Note: If password contains special characters, it might need quoting
        return f"redis://{username}:{password}@{host}:{port}"
    return f"redis://{host}:{port}"

def get_redis_client() -> Redis:
    """Initialize and return a global Redis client using cloud credentials."""
    global _redis_client, _pool
    
    if _redis_client is not None:
        return _redis_client
        
    host = os.getenv("REDIS_HOST", "localhost")
    port = int(os.getenv("REDIS_PORT", "6379"))
    username = os.getenv("REDIS_USERNAME", "default")
    password = os.getenv("REDIS_PASSWORD", "")
    
    try:
        # Use ConnectionPool for better management
        _pool = ConnectionPool(
            host=host,
            port=port,
            username=username,
            password=password,
            decode_responses=True,
            max_connections=10
        )
        _redis_client = Redis(connection_pool=_pool)
        logger.info(f"Redis client initialized connecting to {host}:{port}")
        return _redis_client
    except Exception as exc:
        logger.error(f"Failed to initialize Redis client: {exc}")
        raise

async def cleanup_redis():
    """Cleanup Redis resources."""
    global _redis_client, _pool
    if _redis_client:
        await _redis_client.close()
        _redis_client = None
    if _pool:
        await _pool.disconnect()
        _pool = None
    logger.info("Redis resources cleaned up")
