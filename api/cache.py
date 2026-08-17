"""Basit process-içi TTL cache.

`app.py`'deki `st.cache_data(ttl=...)` sarmalayıcılarının FastAPI
karşılığı: aynı `(args, kwargs)` için sonucu `ttl_seconds` boyunca saklar.
Streamlit'e özgü değildir; yalnızca stdlib `time`/`threading`/`collections`
kullanır. Süreç yeniden başladığında (deploy, reload) sıfırlanır — bu,
önbelleğin zaten "en fazla ttl_seconds kadar bayat" olmasına göre kabul
edilebilir.

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

_DEFAULT_MAX_ENTRIES = 2000


def ttl_cache(ttl_seconds: float, max_entries: int = _DEFAULT_MAX_ENTRIES):
    def decorator(func: Callable) -> Callable:
        store: "OrderedDict[tuple, tuple[float, Any]]" = OrderedDict()
        lock = threading.Lock()

        @wraps(func)
        def wrapper(*args, **kwargs):
            key = (args, tuple(sorted(kwargs.items())))
            now = time.monotonic()
            with lock:
                cached = store.get(key)
                if cached is not None:
                    if cached[0] > now:
                        store.move_to_end(key)
                        return cached[1]
                    del store[key]
            result = func(*args, **kwargs)
            with lock:
                store[key] = (now + ttl_seconds, result)
                store.move_to_end(key)
                while len(store) > max_entries:
                    store.popitem(last=False)
            return result

        return wrapper

    return decorator
