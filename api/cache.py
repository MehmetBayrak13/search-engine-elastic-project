"""Basit process-içi TTL cache.

`app.py`'deki `st.cache_data(ttl=...)` sarmalayıcılarının FastAPI
karşılığı: aynı `(args, kwargs)` için sonucu `ttl_seconds` boyunca saklar.
Streamlit'e özgü değildir; yalnızca stdlib `time`/`threading` kullanır.
Süreç yeniden başladığında (deploy, reload) sıfırlanır — bu, önbelleğin
zaten "en fazla ttl_seconds kadar bayat" olmasına göre kabul edilebilir.
"""

from __future__ import annotations

import threading
import time
from functools import wraps
from typing import Any, Callable


def ttl_cache(ttl_seconds: float):
    def decorator(func: Callable) -> Callable:
        store: dict[tuple, tuple[float, Any]] = {}
        lock = threading.Lock()

        @wraps(func)
        def wrapper(*args, **kwargs):
            key = (args, tuple(sorted(kwargs.items())))
            now = time.monotonic()
            with lock:
                cached = store.get(key)
                if cached is not None and cached[0] > now:
                    return cached[1]
            result = func(*args, **kwargs)
            with lock:
                store[key] = (now + ttl_seconds, result)
            return result

        return wrapper

    return decorator
