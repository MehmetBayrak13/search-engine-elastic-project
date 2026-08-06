"""
FastAPI backend (`api/main.py`) testleri. Gerçek Elastic Cloud'a HİÇBİR
istek atılmaz — `services.search_service._post_search` mock'lanır (aynı
desen `tests/test_pagination.py`de kullanılır).

Bu backend hiçbir arama/autocomplete iş mantığı içermez; tamamı
`services/` katmanındadır (Streamlit UI'ının kullandığıyla AYNI kod
yolu) — bu testler yalnızca HTTP sözleşmesini (status kodları, response
şekli, query param -> servis çağrısı eşlemesi) doğrular.
"""

import pytest
from fastapi.testclient import TestClient

from api.main import app
from services import search_service

client = TestClient(app)


def _mock_post_search(monkeypatch, response):
    def fake_post_search(payload, timeout=20, index=None):
        return response

    monkeypatch.setattr(search_service, "_post_search", fake_post_search)


def _hit(asin="B000TEST", title="Wireless Headphones", score=12.5):
    return {
        "_id": asin,
        "_score": score,
        "_source": {
            "parent_asin": asin,
            "title": title,
            "store": "Acme",
            "main_category": "Electronics",
            "average_rating": 4.5,
            "rating_number": 10,
            "price": 19.99,
            "image_url": "https://example.com/img.jpg",
        },
    }


def test_health_ok():
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True, "error": None}


def test_config_shape():
    response = client.get("/api/config")
    assert response.status_code == 200
    body = response.json()
    for key in ("hero", "labels", "help_text", "messages", "autocomplete_ui", "limits", "pagination"):
        assert key in body
    assert "search_button" in body["labels"]


def test_search_requires_query():
    response = client.get("/api/search", params={"q": "  "})
    assert response.status_code == 400


def test_search_requires_a_lexical_method():
    response = client.get(
        "/api/search",
        params={
            "q": "kamera",
            "enable_phrase": False,
            "enable_multi_match": False,
            "enable_fuzzy": False,
            "enable_exact_asin": False,
        },
    )
    assert response.status_code == 400


def test_search_success(monkeypatch):
    _mock_post_search(
        monkeypatch,
        ({"hits": {"hits": [_hit()], "total": {"value": 1}}}, None),
    )
    response = client.get("/api/search", params={"q": "wireless headphones", "page": 1})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["query"] == "wireless headphones"
    assert len(body["hits"]) == 1
    hit = body["hits"][0]
    assert hit["asin"] == "B000TEST"
    assert hit["product_url"] == "https://www.amazon.com/dp/B000TEST"


def test_search_elasticsearch_error_becomes_502(monkeypatch):
    _mock_post_search(monkeypatch, (None, "Elasticsearch bir hata döndürdü: 500"))
    response = client.get("/api/search", params={"q": "kamera"})
    assert response.status_code == 502


def test_autocomplete_below_min_chars_returns_empty():
    response = client.get("/api/autocomplete", params={"q": "a"})
    assert response.status_code == 200
    assert response.json()["suggestions"] == []


def test_autocomplete_success(monkeypatch):
    _mock_post_search(
        monkeypatch,
        ({"hits": {"hits": [_hit()], "total": {"value": 1}}}, None),
    )
    response = client.get("/api/autocomplete", params={"q": "wireless"})
    assert response.status_code == 200
    suggestions = response.json()["suggestions"]
    assert len(suggestions) == 1
    assert suggestions[0]["asin"] == "B000TEST"


def test_search_without_debug_intent_omits_intent_debug(monkeypatch):
    _mock_post_search(
        monkeypatch,
        ({"hits": {"hits": [_hit()], "total": {"value": 1}}}, None),
    )
    response = client.get("/api/search", params={"q": "wireless mouse"})
    assert response.status_code == 200
    assert "intent_debug" not in response.json()


def test_search_with_debug_intent_true_includes_json_safe_payload(monkeypatch):
    _mock_post_search(
        monkeypatch,
        ({"hits": {"hits": [_hit()], "total": {"value": 1}}}, None),
    )
    response = client.get("/api/search", params={"q": "iphone case", "debug_intent": True})
    assert response.status_code == 200
    body = response.json()
    assert "intent_debug" in body
    debug = body["intent_debug"]
    assert set(debug.keys()) == {"matched_rules", "positive_categories", "negative_categories", "dynamic_discovery", "lexical_required"}
    assert debug["lexical_required"] is True
    assert "legacy_hard_exclusions" not in debug
    import json as json_module
    import re
    serialized = json_module.dumps(body)
    assert "ELASTICSEARCH_API_KEY" not in serialized
    assert not re.search(r"ApiKey [A-Za-z0-9+/=]{10,}", serialized)


def test_autocomplete_elasticsearch_error_becomes_502(monkeypatch):
    # Farklı bir sorgu metni kullanılır: `_fetch_suggestion_hits` process-içi
    # TTL cache'lidir (bkz. api/cache.py) — "wireless" `test_autocomplete_success`
    # tarafından zaten önbelleğe yazıldığından aynı metni kullanmak bu mock'u
    # hiç görmeden önbellekten yanıt döndürür.
    _mock_post_search(monkeypatch, (None, "Elasticsearch bir hata döndürdü: 500"))
    response = client.get("/api/autocomplete", params={"q": "bluetooth speaker"})
    assert response.status_code == 502
