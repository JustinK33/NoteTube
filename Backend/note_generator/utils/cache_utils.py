from typing import Callable, TypeVar, cast
from django.core.cache import cache

T = TypeVar("T")
_MISS = object()


def cached_get_or_set(key: str, timeout: int, compute: Callable[[], T]) -> T:
    value = cache.get(key, _MISS)
    if value is not _MISS:
        # print("CACHE HIT:", key)
        return cast(T, value)
    # if the cache missed just run the orignial operation
    # print("CACHE MISS:", key)
    value = compute()
    cache.set(key, value, timeout=timeout)
    return value
