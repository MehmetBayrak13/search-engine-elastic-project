"""`api/cache.py: ttl_cache` testleri.

Güvenlik regresyon testi içerir: `/api/search`/`/api/autocomplete` bu
dekoratörü doğrudan kullanıcı girdisi olan `q` metnini önbellek anahtarının
parçası olarak kullanır (bkz. api/main.py), bu yüzden `max_entries` sınırı
olmadan çok sayıda benzersiz sorgu süreç belleğini sınırsızca büyütebilir
(bkz. api/cache.py docstring'i).
"""

from __future__ import annotations

import time

from api.cache import ttl_cache


def test_ttl_cache_returns_cached_result_within_ttl():
    calls = {"n": 0}

    @ttl_cache(60)
    def fetch(x):
        calls["n"] += 1
        return x * 2

    assert fetch(5) == 10
    assert fetch(5) == 10
    assert calls["n"] == 1


def test_ttl_cache_recomputes_after_expiry():
    calls = {"n": 0}

    @ttl_cache(0.01)
    def fetch(x):
        calls["n"] += 1
        return x * 2

    fetch(5)
    time.sleep(0.03)
    fetch(5)
    assert calls["n"] == 2


def test_ttl_cache_distinguishes_different_args():
    calls = {"n": 0}

    @ttl_cache(60)
    def fetch(x):
        calls["n"] += 1
        return x * 2

    fetch(5)
    fetch(6)
    assert calls["n"] == 2


def test_ttl_cache_bounds_memory_growth_with_many_distinct_keys():
    # Regresyon: `q` kullanıcı girdisidir -- çok sayıda benzersiz sorgu
    # gönderen bir istemci, üst sınır olmadan önbelleği sınırsızca
    # büyütebilirdi (bkz. modül docstring'i). Burada iç `store`'a doğrudan
    # erişmek yerine davranışı gözlemliyoruz: en eski anahtarlar tahliye
    # edilmeli, en yeniler önbellekte kalmalı.
    calls = {"n": 0}

    @ttl_cache(60, max_entries=100)
    def fetch(x):
        calls["n"] += 1
        return x

    for i in range(1000):
        fetch(i)
    assert calls["n"] == 1000

    # En son 100 anahtar hâlâ önbellekte olmalı (tekrar çağrıldığında
    # `func` yeniden ÇALIŞTIRILMAMALI).
    calls["n"] = 0
    for i in range(900, 1000):
        fetch(i)
    assert calls["n"] == 0

    # Çok daha önceki anahtarlar tahliye edilmiş olmalı (tekrar
    # çağrıldığında `func` yeniden çalıştırılmalı).
    calls["n"] = 0
    fetch(0)
    assert calls["n"] == 1


def test_ttl_cache_lru_touch_keeps_recently_used_entry_alive():
    calls = {"n": 0}

    @ttl_cache(60, max_entries=2)
    def fetch(x):
        calls["n"] += 1
        return x

    fetch("a")
    fetch("b")
    fetch("a")  # "a" en son kullanılan -> LRU sırasında sona taşınır
    fetch("c")  # kapasite dolu, en eski kullanılan ("b") tahliye edilmeli

    calls["n"] = 0
    fetch("a")
    assert calls["n"] == 0  # hâlâ önbellekte

    calls["n"] = 0
    fetch("b")
    assert calls["n"] == 1  # tahliye edildi, yeniden hesaplandı
