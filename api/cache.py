"""Basit process-içi TTL cache.

FastAPI backend'inin (`api/main.py`) kullandığı önbellekleme mekanizması:
aynı `(args, kwargs)` için sonucu `ttl_seconds` boyunca saklar. Yalnızca
stdlib `time`/`threading`/`collections` kullanır. Süreç yeniden başladığında
(deploy, reload) sıfırlanır — bu, önbelleğin zaten "en fazla ttl_seconds
kadar bayat" olmasına göre kabul edilebilir.

`max_entries`: bu fonksiyon `/api/search` ve `/api/autocomplete`de
DOĞRUDAN kullanıcı girdisi olan `q` metnini (bkz. api/main.py
`_fetch_category_aggregations`/`_fetch_suggestion_hits`) önbellek
anahtarının parçası olarak kullanır. Bir üst sınır olmadan, her benzersiz
sorgu metni süresi dolana kadar (ve süresi dolan girdiler yalnızca AYNI
anahtar tekrar istendiğinde silindiği için, pratikte sıklıkla süresiz)
belleğe eklenir — çok sayıda benzersiz `q` değeriyle gelen bir istemci
(kötü niyetli veya değil) süreç belleğini sınırsızca büyütüp OOM'a yol
açabilir. LRU tahliyesi (OrderedDict) bunu sabit bir üst sınırla önler.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from functools import wraps
from typing import Any, Callable

from opentelemetry import trace

_DEFAULT_MAX_ENTRIES = 2000


def ttl_cache(ttl_seconds: float, max_entries: int = _DEFAULT_MAX_ENTRIES, name: str | None = None):
    """`name` verilirse, her çağrıda geçerli OTel span'ine
    `cache.<name>.hit` (bool) attribute'u eklenir -- Honeycomb'da önbellek
    isabet oranını görmek için. `trace.get_current_span()` aktif bir span
    yokken no-op bir span döner, bu yüzden testlerde/OTel'siz çalıştırmada
    güvenlidir."""

    def decorator(func: Callable) -> Callable:
        store: "OrderedDict[tuple, tuple[float, Any]]" = OrderedDict()
        lock = threading.Lock()
        cache_name = name or func.__name__

        @wraps(func)
        def wrapper(*args, **kwargs):
            key = (args, tuple(sorted(kwargs.items())))
            now = time.monotonic()
            with lock:
                cached = store.get(key)
                if cached is not None:
                    if cached[0] > now:
                        store.move_to_end(key)
                        trace.get_current_span().set_attribute(f"cache.{cache_name}.hit", True)
                        return cached[1]
                    del store[key]
            trace.get_current_span().set_attribute(f"cache.{cache_name}.hit", False)
            result = func(*args, **kwargs)
            with lock:
                store[key] = (now + ttl_seconds, result)
                store.move_to_end(key)
                while len(store) > max_entries:
                    store.popitem(last=False)
            return result

        return wrapper

    return decorator
