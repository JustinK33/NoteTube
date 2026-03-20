from typing import Callable, TypeVar, cast
from django.core.cache import cache
import logging

T = TypeVar("T")
_MISS = object()
logger = logging.getLogger(__name__)


def safe_cache_get(key: str, default=None):
    try:
        return cache.get(key, default)
    except Exception as e:
        logger.warning(f"Cache get failed for key {key}: {e}")
        return default


def safe_cache_set(key: str, value, timeout: int | None = None) -> bool:
    try:
        cache.set(key, value, timeout=timeout)
        return True
    except Exception as e:
        logger.warning(f"Cache set failed for key {key}: {e}")
        return False


def safe_cache_delete(key: str) -> bool:
    try:
        cache.delete(key)
        return True
    except Exception as e:
        logger.warning(f"Cache delete failed for key {key}: {e}")
        return False


def cached_get_or_set(key: str, timeout: int, compute: Callable[[], T]) -> T:
    value = safe_cache_get(key, _MISS)
    if value is not _MISS:
        # print("CACHE HIT:", key)
        return cast(T, value)
    # if the cache missed just run the orignial operation
    # print("CACHE MISS:", key)
    value = compute()
    safe_cache_set(key, value, timeout=timeout)
    return value
