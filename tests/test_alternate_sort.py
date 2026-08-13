"""
`search_products`'ın `sort != "relevance"` iken alaka tabanı (min_score)
uygulamak için gönderdiği "probe" isteği testleri (bkz. AlternateSortConfig
docstring'i). Gerçek Elastic Cloud'a HİÇBİR istek atılmaz.

NOT: `search_products` her zaman ÖNCE bir dinamik kategori keşfi aggregation
isteği atar (bkz. resolve_intent_signals), SONRA (sort != relevance ise)
probe, SONRA asıl aramayı. `responses` listesinin ilk elemanı bu yüzden
her zaman boş/no-op bir aggregation yanıtıdır; asıl ilgilenilen çağrılar
`calls[-2]` (probe, varsa) ve `calls[-1]` (asıl arama)'dır.
"""

import dataclasses

import pytest

import app

_DISCOVERY_RESPONSE = ({}, None)


@pytest.fixture(autouse=True)
def _restore_config():
    original = app.search_service.CONFIG
    yield
    app.search_service.CONFIG = original


def _with_alternate_sort(**overrides):
    alt = dataclasses.replace(app.search_service.CONFIG.alternate_sort, **overrides)
    return dataclasses.replace(app.search_service.CONFIG, alternate_sort=alt)


def _mock_sequenced_post_search(monkeypatch, responses):
    """Sıralı `_post_search` çağrılarına, verilen `responses` listesindeki
    yanıtları SIRAYLA döner (ilk çağrı her zaman kategori keşfi)."""
    calls = []

    def fake_post_search(payload, timeout=20, index=None, search_type=None):
        calls.append(payload)
        return responses[len(calls) - 1]

    monkeypatch.setattr(app.search_service, "_post_search", fake_post_search)
    return calls


def test_relevance_sort_makes_no_probe_request(monkeypatch):
    calls = _mock_sequenced_post_search(
        monkeypatch,
        [_DISCOVERY_RESPONSE, ({"hits": {"hits": [], "total": {"value": 0}, "max_score": None}}, None)],
    )
    app.search_products("kamera", page=1, sort="relevance")
    assert len(calls) == 2  # discovery + asıl arama, probe YOK
    assert "min_score" not in calls[-1]


def test_rating_sort_probes_then_applies_min_score(monkeypatch):
    calls = _mock_sequenced_post_search(
        monkeypatch,
        [
            _DISCOVERY_RESPONSE,
            ({"hits": {"hits": [], "total": {"value": 0}, "max_score": 1000.0}}, None),
            ({"hits": {"hits": [], "total": {"value": 0}}}, None),
        ],
    )
    app.search_products("kamera", page=1, sort="rating")
    assert len(calls) == 3  # discovery + probe + asıl arama
    probe_payload, real_payload = calls[-2], calls[-1]
    assert "sort" not in probe_payload  # probe her zaman relevance (sort anahtarı yok)
    assert probe_payload["size"] == 1
    assert "sort" in real_payload
    ratio = app.search_service.CONFIG.alternate_sort.min_score_ratio
    assert real_payload["min_score"] == pytest.approx(1000.0 * ratio)


def test_price_sort_also_probes(monkeypatch):
    calls = _mock_sequenced_post_search(
        monkeypatch,
        [
            _DISCOVERY_RESPONSE,
            ({"hits": {"hits": [], "total": {"value": 0}, "max_score": 200.0}}, None),
            ({"hits": {"hits": [], "total": {"value": 0}}}, None),
        ],
    )
    app.search_products("kamera", page=1, sort="price-asc")
    assert len(calls) == 3
    assert calls[-1]["min_score"] == pytest.approx(200.0 * app.search_service.CONFIG.alternate_sort.min_score_ratio)


def test_alternate_sort_disabled_skips_probe_and_min_score(monkeypatch):
    app.search_service.CONFIG = _with_alternate_sort(enabled=False)
    calls = _mock_sequenced_post_search(
        monkeypatch, [_DISCOVERY_RESPONSE, ({"hits": {"hits": [], "total": {"value": 0}}}, None)]
    )
    app.search_products("kamera", page=1, sort="rating")
    assert len(calls) == 2  # discovery + asıl arama, probe YOK
    assert "min_score" not in calls[-1]


def test_probe_failure_does_not_block_real_search(monkeypatch):
    # Probe hata dönerse (bkz. _probe_max_relevance_score) taban sessizce
    # atlanır -- normal arama YİNE DE çalışmalı, min_score olmadan.
    calls = _mock_sequenced_post_search(
        monkeypatch,
        [
            _DISCOVERY_RESPONSE,
            (None, "Elasticsearch bir hata döndürdü: 500"),
            ({"hits": {"hits": [], "total": {"value": 0}}}, None),
        ],
    )
    result = app.search_products("kamera", page=1, sort="rating")
    assert len(calls) == 3
    assert "min_score" not in calls[-1]
    assert result.error is None


def test_probe_none_max_score_skips_min_score(monkeypatch):
    # max_score=None (ör. hiç eşleşme yoksa) probe'un anlamsız/kullanılamaz
    # olduğunu belirtir -- min_score eklenmemeli.
    calls = _mock_sequenced_post_search(
        monkeypatch,
        [
            _DISCOVERY_RESPONSE,
            ({"hits": {"hits": [], "total": {"value": 0}, "max_score": None}}, None),
            ({"hits": {"hits": [], "total": {"value": 0}}}, None),
        ],
    )
    app.search_products("kamera", page=1, sort="rating")
    assert "min_score" not in calls[-1]
