import os
import re
import time
import threading
from collections import OrderedDict

CACHE_TTL_SECONDS = int(os.environ.get("CACHE_TTL_SECONDS", "21600"))  # 6 hours. 0 turns it off
CACHE_MAX_ENTRIES = int(os.environ.get("CACHE_MAX_ENTRIES", "500"))

_lock = threading.Lock()
_store: OrderedDict[str, dict] = OrderedDict()


def _key(message: str) -> str:
    return re.sub(r"\s+", " ", message.lower()).strip(" ?!.")


def cache_get(message: str) -> dict | None:
    if CACHE_TTL_SECONDS <= 0:
        return None
    key = _key(message)
    with _lock:
        item = _store.get(key)
        if item is None:
            return None
        if time.time() - item["at"] > CACHE_TTL_SECONDS:
            _store.pop(key, None)
            return None
        _store.move_to_end(key)
        return item["value"]


def cache_put(message: str, value: dict):
    if CACHE_TTL_SECONDS <= 0:
        return
    key = _key(message)
    with _lock:
        _store[key] = {"value": value, "at": time.time()}
        _store.move_to_end(key)
        while len(_store) > CACHE_MAX_ENTRIES:
            _store.popitem(last=False)


def cache_clear():
    """Called whenever notes or images change, so old answers don't linger."""
    with _lock:
        _store.clear()