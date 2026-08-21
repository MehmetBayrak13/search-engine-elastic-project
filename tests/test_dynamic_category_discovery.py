"""
Dynamic category discovery testleri. Gerçek Elastic Cloud'a HİÇBİR istek
atılmaz — `search_service._post_search` mock'lanır (bkz. `_mock_post_search`
fixture). Kategori keşif mantığı `services/search_service.py`de yaşıyor.
"""

import dataclasses

import pytest

from services import autocomplete_service, search_service


def _innermost_query(query_node):
    """Test helper: `build_search_query` wraps the base `{"bool": ...}` query
    in a `field_consensus` `function_score` (Task 3) — and optionally a
    `boosting`/another `function_score` on top of that. Peels back those
    wrappers to reach the actual `bool` query node so structural assertions
    written before Task 3 keep checking real behavior."""
    node = query_node
    while isinstance(node, dict):
        if "function_score" in node:
            node = node["function_score"]["query"]
        elif "boosting" in node:
            node = node["boosting"]["positive"]
        else:
            break
    return node


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


def _mock_post_search(monkeypatch, response_by_index=None, default=(({}), None)):
    """`search_service._post_search`ü mock'lar ve yapılan çağrıları kaydeder."""
    calls = []

    def fake_post_search(payload, timeout=20, index=None, search_type=None):
        calls.append({"payload": payload, "timeout": timeout, "index": index, "search_type": search_type})
        if response_by_index and index in response_by_index:
            return response_by_index[index]
        return default

    monkeypatch.setattr(search_service, "_post_search", fake_post_search)
    return calls


def test_discovery_runs_when_intent_rules_is_empty(monkeypatch):
    monkeypatch.setattr(search_service, "INTENT_RULES", {})
    _mock_post_search(
        monkeypatch,
        {search_service.INDEX_NAME: ({"aggregations": AGGREGATIONS_WITH_TOILET_PAPER}, None)},
    )
    candidates = search_service.discover_category_intent("toilet paper")
    assert candidates, "intent_rules.json boşken de kategori keşfi çalışmalı"


def test_toilet_paper_produces_category_candidate(monkeypatch):
    _mock_post_search(
        monkeypatch,
        {search_service.INDEX_NAME: ({"aggregations": AGGREGATIONS_WITH_TOILET_PAPER}, None)},
    )
    candidates = search_service.discover_category_intent("toilet paper")
    values = {c["value"] for c in candidates}
    assert "Toilet Paper" in values


def test_turkish_translation_is_used_in_discovery_query(monkeypatch):
    calls = _mock_post_search(
        monkeypatch,
        {search_service.INDEX_NAME: ({"aggregations": AGGREGATIONS_WITH_TOILET_PAPER}, None)},
    )
    search_service.discover_category_intent("tuvalet kağıdı")

    assert calls, "discovery sorgusu gönderilmedi"
    payload = calls[0]["payload"]
    should_queries = payload["query"]["bool"]["should"]
    queried_texts = {clause["multi_match"]["query"] for clause in should_queries}
    assert "toilet paper" in queried_texts
    assert "tuvalet kağıdı" in queried_texts


def test_discovery_only_called_by_normal_search_build(monkeypatch):
    calls = []
    monkeypatch.setattr(
        search_service,
        "discover_category_intent",
        lambda query_text, **kwargs: calls.append(query_text) or [],
    )

    autocomplete_service.build_autocomplete_query("kamera", apply_intent_reranking=True)
    assert calls == [], "autocomplete kategori keşfini TETİKLEMEMELİ"

    search_service.resolve_intent_signals("kamera", include_dynamic=True)
    assert calls == ["kamera"], "normal arama akışı kategori keşfini çağırmalı"


def test_autocomplete_query_never_triggers_network_discovery_call(monkeypatch):
    calls = _mock_post_search(monkeypatch)
    autocomplete_service.build_autocomplete_query("kamera")
    called_indexes = {c["index"] for c in calls}
    assert search_service.INDEX_NAME not in called_indexes


def test_discovery_failure_does_not_break_normal_search(monkeypatch):
    _mock_post_search(
        monkeypatch,
        {search_service.INDEX_NAME: (None, "Elasticsearch zaman aşımına uğradı")},
    )
    candidates = search_service.discover_category_intent("toilet paper")
    assert candidates == []

    # search_products'ın kullandığı üst seviye fonksiyon da patlamamalı.
    signals = search_service.resolve_intent_signals("toilet paper", include_dynamic=True)
    payload = search_service.build_search_query("toilet paper", intent_signals=signals)
    assert _innermost_query(payload["query"])["bool"]["must"], "lexical grup hâlâ zorunlu kalmalı"


def test_dynamic_boosts_under_should_lexical_under_must(monkeypatch):
    _mock_post_search(
        monkeypatch,
        {search_service.INDEX_NAME: ({"aggregations": AGGREGATIONS_WITH_TOILET_PAPER}, None)},
    )
    signals = search_service.resolve_intent_signals("toilet paper", include_dynamic=True)
    payload = search_service.build_search_query("toilet paper", intent_signals=signals)
    inner_query = _innermost_query(payload["query"])
    must = inner_query["bool"]["must"]
    should = inner_query["bool"].get("should", [])

    assert "should" in must[0]["bool"], "lexical zorunlu eşleşme bool.must altında olmalı"
    assert any(
        clause.get("term", {}).get("categories", {}).get("value") == "Toilet Paper"
        for clause in should
    ), "dinamik kategori boostu bool.should altında olmalı"


def test_dynamic_intent_disabled_skips_discovery(monkeypatch):
    disabled_config = dataclasses.replace(
        search_service.CONFIG, dynamic_intent=dataclasses.replace(search_service.CONFIG.dynamic_intent, enabled=False)
    )
    monkeypatch.setattr(search_service, "CONFIG", disabled_config)
    calls = _mock_post_search(monkeypatch)

    candidates = search_service.discover_category_intent("toilet paper")
    assert candidates == []
    assert calls == [], "dynamic_intent.enabled=false iken hiç istek atılmamalı"


def test_minimum_query_length_is_enforced(monkeypatch):
    short_min_config = dataclasses.replace(
        search_service.CONFIG,
        dynamic_intent=dataclasses.replace(search_service.CONFIG.dynamic_intent, minimum_query_length=10),
    )
    monkeypatch.setattr(search_service, "CONFIG", short_min_config)
    calls = _mock_post_search(monkeypatch)

    candidates = search_service.discover_category_intent("tv")
    assert candidates == []
    assert calls == [], "minimum sorgu uzunluğu altındaysa istek atılmamalı"


def test_max_category_candidates_limit_is_respected(monkeypatch):
    limited_config = dataclasses.replace(
        search_service.CONFIG,
        dynamic_intent=dataclasses.replace(search_service.CONFIG.dynamic_intent, max_category_candidates=2),
    )
    monkeypatch.setattr(search_service, "CONFIG", limited_config)
    many_buckets = {
        "by_categories": {
            "buckets": [
                {"key": f"Category {i}", "doc_count": 100 - i} for i in range(10)
            ]
        },
        "by_main_category": {"buckets": []},
    }
    _mock_post_search(monkeypatch, {search_service.INDEX_NAME: ({"aggregations": many_buckets}, None)})

    candidates = search_service.discover_category_intent("toilet paper")
    assert len(candidates) <= 2


def test_aggregation_fields_come_from_config():
    payload = search_service.build_category_discovery_query("toilet paper")
    expected_agg_names = set(search_service.CONFIG.dynamic_intent.aggregation_bucket_map.keys())
    assert set(payload["aggs"].keys()) == expected_agg_names


def test_store_field_included_for_brand_query_segmentation():
    # "store" alanı, kullanıcının sorgusunda bir marka geçip geçmediğini
    # (ör. "nike sneakers") reindex GEREKTİRMEDEN, mevcut kategori keşfi
    # altyapısını yeniden kullanarak tespit etmek için eklendi (bkz. CLAUDE.md
    # sorgu segmentasyonu notları). Bu, `by_store` aggregation bucket'ının
    # her zaman üretileceğini kilitler.
    assert "store" in search_service.CONFIG.dynamic_intent.aggregation_fields
    payload = search_service.build_category_discovery_query("nike sneakers")
    assert "by_store" in payload["aggs"]
    assert payload["aggs"]["by_store"]["significant_terms"]["field"] == "store"


def test_discovery_query_is_size_zero_with_short_timeout():
    payload = search_service.build_category_discovery_query("toilet paper")
    assert payload["size"] == 0
    assert payload["track_total_hits"] is False
    assert payload["timeout"] == f"{search_service.CONFIG.dynamic_intent.timeout_seconds}s"


def test_build_category_discovery_query_does_not_raise_with_default_config():
    # Regresyon: production'da CONFIG.quality_ranking eksik/uyumsuzken
    # build_category_discovery_query AttributeError ile çöküyordu.
    # Gerçek (mock'lanmamış) search_service.CONFIG ile çağrılır.
    payload = search_service.build_category_discovery_query("toilet paper")
    assert "query" in payload


def test_search_service_builds_normal_search_query():
    assert search_service.CONFIG is not None
    assert search_service.CONFIG.quality_ranking is not None
    query = search_service.build_search_query("toilet paper")
    assert "bool" in _innermost_query(query["query"])


def test_no_hardcoded_watch_check_in_search_service():
    # Intent tespiti services/search_service.py'de yaşıyor (bkz. proje
    # mimarisi) — koruma amacı: hiçbir "watch" business-rule'u kod içinde
    # sabit yazılı olmamalı, tamamen config/intent_rules.json'dan gelmeli.
    with open(search_service.__file__, "r", encoding="utf-8") as file:
        content = file.read()
    assert '"watch"' not in content
    assert "'watch'" not in content


def test_turkish_suffix_variations_are_not_hardcoded_in_search_service():
    with open(search_service.__file__, "r", encoding="utf-8") as file:
        content = file.read().casefold()
    suffix_variants = [
        "telefonlar", "telefonların", "telefonlarda", "telefonlardan",
        "kitaplar", "kitapların", "kulaklıklar", "kulaklıkların",
        "arabalar", "arabaları",
    ]
    for variant in suffix_variants:
        assert variant not in content, f"'{variant}' search_service.py içinde elle listelenmemeli"
