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
    def fake_post_search(payload, timeout=20, index=None, search_type=None):
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


@pytest.mark.parametrize("path", ["/api/health", "/api/config"])
@pytest.mark.parametrize("origin", ["https://mehmetbayrak.ai", "https://www.mehmetbayrak.ai"])
def test_production_origin_allowed_by_cors(path, origin):
    response = client.get(path, headers={"Origin": origin})
    assert response.headers.get("access-control-allow-origin") == origin


def test_unknown_origin_not_echoed_by_cors():
    response = client.get("/api/health", headers={"Origin": "https://evil.example.com"})
    assert "access-control-allow-origin" not in response.headers


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


def test_search_rejects_unknown_sort_value():
    response = client.get("/api/search", params={"q": "kamera", "sort": "bogus"})
    assert response.status_code == 400


def test_search_accepts_known_sort_values_and_forwards_to_query(monkeypatch):
    captured = {}

    def fake_post_search(payload, timeout=20, index=None, search_type=None):
        captured["sort"] = payload.get("sort")
        return {"hits": {"hits": [], "total": {"value": 0}}}, None

    monkeypatch.setattr(search_service, "_post_search", fake_post_search)
    response = client.get("/api/search", params={"q": "kamera", "sort": "price-asc"})
    assert response.status_code == 200
    assert captured["sort"] == [{"price": {"order": "asc", "missing": "_last"}}, "_score"]


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


def test_debug_relevance_absent_by_default(monkeypatch):
    _mock_post_search(
        monkeypatch,
        ({"hits": {"hits": [_hit()], "total": {"value": 1}}}, None),
    )
    response = client.get("/api/search", params={"q": "wireless mouse"})
    assert response.status_code == 200
    assert "relevance_debug" not in response.json()


def test_debug_relevance_true_adds_bounded_relevance_debug(monkeypatch):
    # `compute_relevance_explain` artık ES'in `matched_queries`sine değil,
    # hit'in `_source` metnine bakar (bkz. services/search_service.py
    # docstring'i) — bu yüzden hit'in title/features alanları sorgu
    # kelimelerini gerçekten içermeli.
    hit = _hit(title="Wireless Mouse")
    hit["_source"]["features"] = "Ergonomic wireless mouse with adjustable DPI"
    _mock_post_search(
        monkeypatch,
        ({"hits": {"hits": [hit], "total": {"value": 1}}}, None),
    )
    response = client.get("/api/search", params={"q": "wireless mouse", "debug_relevance": True})
    assert response.status_code == 200
    body = response.json()
    assert "relevance_debug" in body
    assert len(body["relevance_debug"]) == len(body["hits"]) == 1
    entry = body["relevance_debug"][0]
    assert set(entry.keys()) == {
        "matched_fields", "consensus_level", "contradictions", "applied_penalty", "translation_matched",
    }
    assert entry["matched_fields"] == ["title", "features"]
    assert entry["consensus_level"] == 2
    assert entry["contradictions"] == []
    assert entry["applied_penalty"] == 1.0
    assert entry["translation_matched"] is False


def test_debug_relevance_bounded_to_explain_top_n(monkeypatch):
    hits = [_hit(asin=f"B{i:09d}", title=f"Item {i}") for i in range(25)]
    for hit in hits:
        hit["matched_queries"] = ["field:title"]
    _mock_post_search(
        monkeypatch,
        ({"hits": {"hits": hits, "total": {"value": 25}}}, None),
    )
    response = client.get("/api/search", params={"q": "wireless mouse", "debug_relevance": True})
    assert response.status_code == 200
    body = response.json()
    # config/search_config.json's relevance_debug.explain_top_n default is 20
    assert len(body["relevance_debug"]) == 20
    assert len(body["hits"]) == 25


def test_debug_relevance_reports_contradiction_penalty(monkeypatch):
    # "men perfume" -> intent_rules.json'daki "men_perfume" kuralını tetikler
    # (contradiction_terms: moisturizer, body lotion, ...). description ve
    # features bu terimlerden birer tanesini taşıyor (2 alan -> mild tier).
    # `compute_relevance_explain` bu çelişkiyi ES'in `matched_queries`sinden
    # değil, hit'in `_source` metninden hesaplar (bkz. o fonksiyonun
    # docstring'i).
    hit = _hit(title="Men's Cologne Perfume Spray")
    hit["_source"]["description"] = "Daily moisturizer for men"
    hit["_source"]["features"] = "Body lotion set included"
    _mock_post_search(
        monkeypatch,
        ({"hits": {"hits": [hit], "total": {"value": 1}}}, None),
    )
    response = client.get("/api/search", params={"q": "men perfume", "debug_relevance": True})
    assert response.status_code == 200
    entry = response.json()["relevance_debug"][0]
    assert set(entry["contradictions"]) == {"description", "features"}
    assert entry["applied_penalty"] < 1.0


def _innermost_query(query_node):
    """Same unwrap helper as tests/test_query_builders.py (duplicated locally
    -- each test file mocks/asserts independently by convention in this repo,
    see e.g. `_mock_post_search` being redefined per file rather than shared)."""
    node = query_node
    while isinstance(node, dict):
        if "function_score" in node:
            node = node["function_score"]["query"]
        elif "boosting" in node:
            node = node["boosting"]["positive"]
        else:
            break
    return node


def _capture_payloads(monkeypatch, response_hits=None):
    """Records every payload sent to `_post_search` during one API call --
    a single /api/search request can trigger up to three real ES calls
    (dynamic category discovery aggregation `size:0`, an alternate-sort
    `min_score` probe `size:1`, and the real search `size:page_size`), so
    callers must pick the right one out of `captured` (see `_main_payload`)."""
    captured = []
    hits_payload = response_hits if response_hits is not None else {"hits": [], "total": {"value": 0}}

    def fake_post_search(payload, timeout=20, index=None, search_type=None):
        captured.append(payload)
        if payload.get("size") == 0:
            return {"aggregations": {}}, None
        return {"hits": hits_payload}, None

    monkeypatch.setattr(search_service, "_post_search", fake_post_search)
    return captured


def _main_payload(captured):
    page_size = search_service.CONFIG.pagination.page_size
    return next(p for p in captured if p.get("size") == page_size)


def test_search_forwards_enable_phrase_toggle(monkeypatch):
    # Proves the HTTP query param actually reaches `build_search_query` --
    # service-level tests (tests/test_query_builders.py) already prove the
    # *query-building* logic for each toggle is correct; this proves the
    # *wiring* from api/main.py's `enable_phrase: bool = Query(...)` through
    # `search_products` doesn't have a param-name mismatch/typo.
    captured_with = _capture_payloads(monkeypatch)
    client.get("/api/search", params={"q": "gadget"})
    with_should = _innermost_query(_main_payload(captured_with)["query"])["bool"]["must"][0]["bool"]["should"]

    captured_without = _capture_payloads(monkeypatch)
    client.get("/api/search", params={"q": "gadget", "enable_phrase": False})
    without_should = _innermost_query(_main_payload(captured_without)["query"])["bool"]["must"][0]["bool"]["should"]

    assert len(without_should) == len(with_should) - 1


def test_search_forwards_enable_exact_asin_toggle(monkeypatch):
    captured_with = _capture_payloads(monkeypatch)
    client.get("/api/search", params={"q": "gadget"})
    with_should = _innermost_query(_main_payload(captured_with)["query"])["bool"]["must"][0]["bool"]["should"]

    captured_without = _capture_payloads(monkeypatch)
    client.get("/api/search", params={"q": "gadget", "enable_exact_asin": False})
    without_should = _innermost_query(_main_payload(captured_without)["query"])["bool"]["must"][0]["bool"]["should"]

    assert len(without_should) == len(with_should) - 1


def test_search_forwards_enable_fuzzy_toggle(monkeypatch):
    captured_with = _capture_payloads(monkeypatch)
    client.get("/api/search", params={"q": "gadget"})
    with_should = _innermost_query(_main_payload(captured_with)["query"])["bool"]["must"][0]["bool"]["should"]

    captured_without = _capture_payloads(monkeypatch)
    client.get("/api/search", params={"q": "gadget", "enable_fuzzy": False})
    without_should = _innermost_query(_main_payload(captured_without)["query"])["bool"]["must"][0]["bool"]["should"]

    assert len(without_should) == len(with_should) - 1


def test_search_forwards_enable_multi_match_toggle(monkeypatch):
    # field_relevance contributes one clause per configured field plus one
    # cross_fields fallback clause (see test_field_relevance_disabled_by_enable_multi_match_toggle
    # in test_query_builders.py for the same assertion at the service level).
    captured_with = _capture_payloads(monkeypatch)
    client.get("/api/search", params={"q": "gadget"})
    with_should = _innermost_query(_main_payload(captured_with)["query"])["bool"]["must"][0]["bool"]["should"]

    captured_without = _capture_payloads(monkeypatch)
    client.get("/api/search", params={"q": "gadget", "enable_multi_match": False})
    without_should = _innermost_query(_main_payload(captured_without)["query"])["bool"]["must"][0]["bool"]["should"]

    expected_delta = len(search_service.CONFIG.field_relevance.fields) + 1
    assert len(without_should) == len(with_should) - expected_delta


def test_search_forwards_page_param(monkeypatch):
    captured = _capture_payloads(monkeypatch)
    response = client.get("/api/search", params={"q": "gadget", "page": 2})
    assert response.status_code == 200
    page_size = search_service.CONFIG.pagination.page_size
    assert _main_payload(captured)["from"] == page_size  # (page 2 - 1) * page_size


def test_search_forwards_price_desc_sort(monkeypatch):
    captured = _capture_payloads(monkeypatch)
    response = client.get("/api/search", params={"q": "gadget", "sort": "price-desc"})
    assert response.status_code == 200
    assert _main_payload(captured)["sort"] == [{"price": {"order": "desc", "missing": "_last"}}, "_score"]


def test_search_forwards_rating_sort_as_bayesian_script(monkeypatch):
    # "rating" is deliberately absent from `_SORT_CLAUSES` (see its comment)
    # -- it produces a Painless `_script` sort, not a static field sort.
    captured = _capture_payloads(monkeypatch)
    response = client.get("/api/search", params={"q": "gadget", "sort": "rating"})
    assert response.status_code == 200
    sort_clause = _main_payload(captured)["sort"]
    assert isinstance(sort_clause, list)
    assert "_script" in sort_clause[0]


def test_debug_relevance_response_has_no_secret_shaped_strings(monkeypatch):
    import re

    hit = _hit()
    hit["matched_queries"] = ["field:title"]
    _mock_post_search(
        monkeypatch,
        ({"hits": {"hits": [hit], "total": {"value": 1}}}, None),
    )
    response = client.get("/api/search", params={"q": "wireless mouse", "debug_relevance": True})
    body_text = response.text
    assert not re.search(r"ELASTICSEARCH_(URL|API_KEY)", body_text)
    assert "ApiKey " not in body_text
