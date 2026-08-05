"""
Dynamic category discovery testleri. Gerçek Elastic Cloud'a HİÇBİR istek
atılmaz — `app.search_service._post_search` mock'lanır (bkz. `_mock_post_search`
fixture). Kategori keşif mantığı `services/search_service.py`de yaşıyor;
`CONFIG`/`INTENT_RULES`/`_post_search`/`discover_category_intent` gibi
mutable/monkeypatch edilebilecek isimler `app.CONFIG` değil
`app.search_service.CONFIG` üzerinden değiştirilmeli (bkz. tests/test_pagination.py
başındaki aynı gerekçe).
"""

import dataclasses

import pytest

import app


AGGREGATIONS_WITH_TOILET_PAPER = {
    "by_categories": {
        "buckets": [
            {"key": "Toilet Paper", "doc_count": 120},
            {"key": "Bath Tissue", "doc_count": 40},
        ]
    },
    "by_main_category": {
        "buckets": [{"key": "Health & Household", "doc_count": 200}]
    },
}


@pytest.fixture(autouse=True)
def _clear_discovery_cache():
    # st.cache_data process genelinde kalıcıdır; testler arasında sızıntıyı
    # önlemek için her testten önce temizle.
    app._fetch_category_aggregations.clear()
    yield
    app._fetch_category_aggregations.clear()


def _mock_post_search(monkeypatch, response_by_index=None, default=(({}), None)):
    """`app.search_service._post_search`ü mock'lar ve yapılan çağrıları kaydeder."""
    calls = []

    def fake_post_search(payload, timeout=20, index=None):
        calls.append({"payload": payload, "timeout": timeout, "index": index})
        if response_by_index and index in response_by_index:
            return response_by_index[index]
        return default

    monkeypatch.setattr(app.search_service, "_post_search", fake_post_search)
    return calls


def test_discovery_runs_when_intent_rules_is_empty(monkeypatch):
    monkeypatch.setattr(app.search_service, "INTENT_RULES", {})
    _mock_post_search(
        monkeypatch,
        {app.INDEX_NAME: ({"aggregations": AGGREGATIONS_WITH_TOILET_PAPER}, None)},
    )
    candidates = app.discover_category_intent("toilet paper")
    assert candidates, "intent_rules.json boşken de kategori keşfi çalışmalı"


def test_toilet_paper_produces_category_candidate(monkeypatch):
    _mock_post_search(
        monkeypatch,
        {app.INDEX_NAME: ({"aggregations": AGGREGATIONS_WITH_TOILET_PAPER}, None)},
    )
    candidates = app.discover_category_intent("toilet paper")
    values = {c["value"] for c in candidates}
    assert "Toilet Paper" in values


def test_turkish_translation_is_used_in_discovery_query(monkeypatch):
    calls = _mock_post_search(
        monkeypatch,
        {app.INDEX_NAME: ({"aggregations": AGGREGATIONS_WITH_TOILET_PAPER}, None)},
    )
    app.discover_category_intent("tuvalet kağıdı")

    assert calls, "discovery sorgusu gönderilmedi"
    payload = calls[0]["payload"]
    should_queries = payload["query"]["bool"]["should"]
    queried_texts = {clause["multi_match"]["query"] for clause in should_queries}
    assert "toilet paper" in queried_texts
    assert "tuvalet kağıdı" in queried_texts


def test_discovery_only_called_by_normal_search_build(monkeypatch):
    calls = []
    monkeypatch.setattr(
        app.search_service,
        "discover_category_intent",
        lambda query_text, **kwargs: calls.append(query_text) or [],
    )

    app.build_autocomplete_query("kamera", apply_intent_reranking=True)
    assert calls == [], "autocomplete kategori keşfini TETİKLEMEMELİ"

    app.resolve_intent_signals("kamera", include_dynamic=True)
    assert calls == ["kamera"], "normal arama akışı kategori keşfini çağırmalı"


def test_autocomplete_query_never_triggers_network_discovery_call(monkeypatch):
    calls = _mock_post_search(monkeypatch)
    app.build_autocomplete_query("kamera")
    called_indexes = {c["index"] for c in calls}
    assert app.INDEX_NAME not in called_indexes


def test_discovery_failure_does_not_break_normal_search(monkeypatch):
    _mock_post_search(
        monkeypatch,
        {app.INDEX_NAME: (None, "Elasticsearch zaman aşımına uğradı")},
    )
    candidates = app.discover_category_intent("toilet paper")
    assert candidates == []

    # search_products'ın kullandığı üst seviye fonksiyon da patlamamalı.
    boost_queries, exclusions = app.resolve_intent_signals("toilet paper", include_dynamic=True)
    payload = app.build_search_query(
        "toilet paper", intent_boost_queries=boost_queries, intent_exclusions=exclusions
    )
    assert payload["query"]["bool"]["must"], "lexical grup hâlâ zorunlu kalmalı"


def test_dynamic_boosts_under_should_lexical_under_must(monkeypatch):
    _mock_post_search(
        monkeypatch,
        {app.INDEX_NAME: ({"aggregations": AGGREGATIONS_WITH_TOILET_PAPER}, None)},
    )
    boost_queries, exclusions = app.resolve_intent_signals("toilet paper", include_dynamic=True)
    payload = app.build_search_query(
        "toilet paper", intent_boost_queries=boost_queries, intent_exclusions=exclusions
    )
    must = payload["query"]["bool"]["must"]
    should = payload["query"]["bool"].get("should", [])

    assert "should" in must[0]["bool"], "lexical zorunlu eşleşme bool.must altında olmalı"
    assert any(
        clause.get("term", {}).get("categories", {}).get("value") == "Toilet Paper"
        for clause in should
    ), "dinamik kategori boostu bool.should altında olmalı"


def test_dynamic_intent_disabled_skips_discovery(monkeypatch):
    disabled_config = dataclasses.replace(
        app.search_service.CONFIG, dynamic_intent=dataclasses.replace(app.search_service.CONFIG.dynamic_intent, enabled=False)
    )
    monkeypatch.setattr(app.search_service, "CONFIG", disabled_config)
    calls = _mock_post_search(monkeypatch)

    candidates = app.discover_category_intent("toilet paper")
    assert candidates == []
    assert calls == [], "dynamic_intent.enabled=false iken hiç istek atılmamalı"


def test_minimum_query_length_is_enforced(monkeypatch):
    short_min_config = dataclasses.replace(
        app.search_service.CONFIG,
        dynamic_intent=dataclasses.replace(app.search_service.CONFIG.dynamic_intent, minimum_query_length=10),
    )
    monkeypatch.setattr(app.search_service, "CONFIG", short_min_config)
    calls = _mock_post_search(monkeypatch)

    candidates = app.discover_category_intent("tv")
    assert candidates == []
    assert calls == [], "minimum sorgu uzunluğu altındaysa istek atılmamalı"


def test_max_category_candidates_limit_is_respected(monkeypatch):
    limited_config = dataclasses.replace(
        app.search_service.CONFIG,
        dynamic_intent=dataclasses.replace(app.search_service.CONFIG.dynamic_intent, max_category_candidates=2),
    )
    monkeypatch.setattr(app.search_service, "CONFIG", limited_config)
    many_buckets = {
        "by_categories": {
            "buckets": [
                {"key": f"Category {i}", "doc_count": 100 - i} for i in range(10)
            ]
        },
        "by_main_category": {"buckets": []},
    }
    _mock_post_search(monkeypatch, {app.INDEX_NAME: ({"aggregations": many_buckets}, None)})

    candidates = app.discover_category_intent("toilet paper")
    assert len(candidates) <= 2


def test_aggregation_fields_come_from_config():
    payload = app.build_category_discovery_query("toilet paper")
    expected_agg_names = set(app.search_service.CONFIG.dynamic_intent.aggregation_bucket_map.keys())
    assert set(payload["aggs"].keys()) == expected_agg_names


def test_discovery_query_is_size_zero_with_short_timeout():
    payload = app.build_category_discovery_query("toilet paper")
    assert payload["size"] == 0
    assert payload["track_total_hits"] is False
    assert payload["timeout"] == f"{app.search_service.CONFIG.dynamic_intent.timeout_seconds}s"


def test_build_category_discovery_query_does_not_raise_with_default_config():
    # Regresyon: production'da CONFIG.quality_ranking eksik/uyumsuzken
    # build_category_discovery_query AttributeError ile çöküyordu.
    # Gerçek (mock'lanmamış) app.search_service.CONFIG ile çağrılır.
    payload = app.build_category_discovery_query("toilet paper")
    assert "query" in payload


def test_app_imports_and_builds_normal_search_query():
    assert app.search_service.CONFIG is not None
    assert app.search_service.CONFIG.quality_ranking is not None
    query = app.build_search_query("toilet paper")
    assert "bool" in query["query"]


def test_no_hardcoded_watch_check_in_app_py():
    # Intent tespiti artık app.py'de değil services/search_service.py'de
    # yaşıyor (bkz. proje mimarisi) — koruma amacı aynı: hiçbir "watch"
    # business-rule'u kod içinde sabit yazılı olmamalı, tamamen
    # config/intent_rules.json'dan gelmeli.
    for source in (app.__file__, app.search_service.__file__):
        with open(source, "r", encoding="utf-8") as file:
            content = file.read()
        assert '"watch"' not in content
        assert "'watch'" not in content


def test_turkish_suffix_variations_are_not_hardcoded_in_app_py():
    with open(app.search_service.__file__, "r", encoding="utf-8") as file:
        content = file.read().casefold()
    suffix_variants = [
        "telefonlar", "telefonların", "telefonlarda", "telefonlardan",
        "kitaplar", "kitapların", "kulaklıklar", "kulaklıkların",
        "arabalar", "arabaları",
    ]
    for variant in suffix_variants:
        assert variant not in content, f"'{variant}' app.py içinde elle listelenmemeli"
