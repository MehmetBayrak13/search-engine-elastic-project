"""
`app.py` içindeki kalite (product_quality) reranking entegrasyonu testleri.
Gerçek Elastic Cloud'a HİÇBİR istek atılmaz.
"""

import dataclasses

import pytest

import app


def _with_quality_ranking(**overrides):
    """`search_service.CONFIG.quality_ranking`ı geçici olarak override eden
    bir AppConfig döner. `build_search_query` artık `services/search_service.py`de
    yaşıyor ve kendi modülünün `CONFIG` adını okuyor — bu yüzden mutasyon
    `app.CONFIG` değil `app.search_service.CONFIG` üzerinde yapılmalı."""
    qr = dataclasses.replace(app.search_service.CONFIG.quality_ranking, **overrides)
    return dataclasses.replace(app.search_service.CONFIG, quality_ranking=qr)


@pytest.fixture(autouse=True)
def _restore_config():
    original = app.search_service.CONFIG
    yield
    app.search_service.CONFIG = original


def test_quality_ranking_disabled_by_default_leaves_query_unchanged():
    assert app.search_service.CONFIG.quality_ranking.enabled is False
    payload = app.build_search_query("wireless headphones", apply_intent_reranking=False)
    assert "function_score" not in payload["query"]
    assert "bool" in payload["query"]


def test_quality_ranking_enabled_wraps_query_in_function_score():
    app.search_service.CONFIG = _with_quality_ranking(enabled=True)
    payload = app.build_search_query("wireless headphones", apply_intent_reranking=False)
    assert "function_score" in payload["query"]
    fs = payload["query"]["function_score"]
    assert "bool" in fs["query"]
    assert fs["score_mode"] == "multiply"
    assert fs["boost_mode"] == "multiply"


def test_quality_ranking_does_not_touch_lexical_must_group():
    app.search_service.CONFIG = _with_quality_ranking(enabled=True)
    payload = app.build_search_query("wireless headphones", apply_intent_reranking=False)
    inner_bool = payload["query"]["function_score"]["query"]["bool"]
    must = inner_bool["must"]
    assert len(must) == 1
    assert "should" in must[0]["bool"]
    assert must[0]["bool"]["minimum_should_match"] == 1


def test_quality_ranking_no_script_score_used():
    app.search_service.CONFIG = _with_quality_ranking(enabled=True)
    payload = app.build_search_query("wireless headphones", apply_intent_reranking=False)
    functions = payload["query"]["function_score"]["functions"]
    for function in functions:
        assert "script_score" not in function


def test_quality_ranking_uses_configured_score_and_consistency_fields():
    app.search_service.CONFIG = _with_quality_ranking(enabled=True, score_field="dq_score", consistency_field="tc_consistency")
    payload = app.build_search_query("wireless headphones", apply_intent_reranking=False)
    functions = payload["query"]["function_score"]["functions"]
    referenced_fields = set()
    for function in functions:
        if "field_value_factor" in function:
            referenced_fields.add(function["field_value_factor"]["field"])
    assert referenced_fields == {"dq_score", "tc_consistency"}


def test_low_consistency_penalty_function_uses_configured_threshold_and_weight():
    app.search_service.CONFIG = _with_quality_ranking(
        enabled=True, low_consistency_threshold=0.4, low_consistency_penalty=0.6, bypass_for_exact_asin=False
    )
    payload = app.build_search_query("wireless headphones", apply_intent_reranking=False)
    functions = payload["query"]["function_score"]["functions"]
    penalty_functions = [f for f in functions if "weight" in f]
    assert len(penalty_functions) == 1
    penalty = penalty_functions[0]
    assert penalty["weight"] == 0.6
    range_clause = penalty["filter"]["bool"]["must"][1]["range"]
    field = next(iter(range_clause))
    assert range_clause[field]["lt"] == 0.4


def test_missing_quality_fields_never_break_query_shape_when_enabled():
    # Eski (henüz reindex edilmemiş) belgelerde kalite alanı yoktur; bu durum
    # sorgunun kendisini bozmamalı — field_value_factor "missing" ile veya
    # exists filtresiyle güvenli biçimde nötr davranır.
    app.search_service.CONFIG = _with_quality_ranking(enabled=True, missing_value_behavior="neutral")
    payload = app.build_search_query("wireless headphones", apply_intent_reranking=False)
    functions = payload["query"]["function_score"]["functions"]
    for function in functions:
        if "field_value_factor" in function:
            assert "missing" in function["field_value_factor"]


def test_missing_value_behavior_skip_uses_exists_filter():
    app.search_service.CONFIG = _with_quality_ranking(enabled=True, missing_value_behavior="skip", bypass_for_exact_asin=False)
    payload = app.build_search_query("wireless headphones", apply_intent_reranking=False)
    functions = payload["query"]["function_score"]["functions"]
    factor_functions = [f for f in functions if "field_value_factor" in f]
    for function in factor_functions:
        assert "missing" not in function["field_value_factor"]
        assert function["filter"]["exists"]["field"] == function["field_value_factor"]["field"]


def test_exact_asin_bypass_excludes_matching_document_from_all_functions():
    app.search_service.CONFIG = _with_quality_ranking(enabled=True, bypass_for_exact_asin=True)
    payload = app.build_search_query("B000123456", enable_exact_asin=True, apply_intent_reranking=False)
    functions = payload["query"]["function_score"]["functions"]
    for function in functions:
        filt = function["filter"]
        must_not = filt.get("bool", filt).get("must_not") if isinstance(filt, dict) else None
        assert must_not, f"beklenen bypass must_not bulunamadı: {function}"
        assert must_not[0]["term"][app.search_service.CONFIG.search_methods.exact_asin.field]["value"] == "B000123456"


def test_bypass_disabled_does_not_add_must_not_filter():
    app.search_service.CONFIG = _with_quality_ranking(enabled=True, bypass_for_exact_asin=False)
    payload = app.build_search_query("B000123456", enable_exact_asin=True, apply_intent_reranking=False)
    functions = payload["query"]["function_score"]["functions"]
    for function in functions:
        filt = function.get("filter")
        if filt and "bool" in filt:
            assert "must_not" not in filt["bool"]


def test_exact_asin_disabled_flag_skips_bypass_even_if_configured():
    app.search_service.CONFIG = _with_quality_ranking(enabled=True, bypass_for_exact_asin=True)
    payload = app.build_search_query("wireless headphones", enable_exact_asin=False, apply_intent_reranking=False)
    functions = payload["query"]["function_score"]["functions"]
    for function in functions:
        filt = function.get("filter")
        if filt and "bool" in filt:
            assert "must_not" not in filt["bool"]


def test_match_none_is_not_wrapped_in_function_score():
    # title_ranking's exact/prefix tiers are an independent lexical method
    # (gated only by config, not by these enable_* switches), so they must
    # also be disabled to exercise the true "no lexical method" safety net.
    cfg = _with_quality_ranking(enabled=True)
    cfg = dataclasses.replace(cfg, title_ranking=dataclasses.replace(cfg.title_ranking, enabled=False))
    app.search_service.CONFIG = cfg
    payload = app.build_search_query(
        "kamera",
        enable_phrase=False,
        enable_multi_match=False,
        enable_fuzzy=False,
        enable_exact_asin=False,
    )
    assert payload["query"] == {"match_none": {}}


def test_turkish_expansion_unaffected_by_quality_ranking():
    app.search_service.CONFIG = _with_quality_ranking(enabled=True)
    payload = app.build_search_query("kablosuz kulaklık", apply_intent_reranking=False)
    lexical = payload["query"]["function_score"]["query"]["bool"]["must"][0]["bool"]["should"]
    assert len(lexical) >= 5  # 4 standart lexical + en az 1 çeviri alternatifi


def test_autocomplete_query_unaffected_by_quality_ranking():
    app.search_service.CONFIG = _with_quality_ranking(enabled=True)
    payload = app.build_autocomplete_query("kam", apply_intent_reranking=False)
    assert "function_score" not in payload["query"]
    assert "bool" in payload["query"]


# ---------------------------------------------------------------------------
# Dinamik kategori keşfi kalite filtresi (§8)
# ---------------------------------------------------------------------------

def test_discovery_quality_filter_disabled_by_default():
    assert app.search_service.CONFIG.quality_ranking.discovery_filter_enabled is False
    payload = app.build_category_discovery_query("toilet paper")
    assert "must_not" not in payload["query"].get("bool", {})


def test_discovery_quality_filter_adds_must_not_range_when_enabled():
    app.search_service.CONFIG = _with_quality_ranking(discovery_filter_enabled=True, discovery_min_data_quality_score=0.4)
    payload = app.build_category_discovery_query("toilet paper")
    must_not = payload["query"]["bool"]["must_not"]
    assert must_not[0]["range"][app.search_service.CONFIG.quality_ranking.score_field]["lt"] == 0.4


def test_discovery_quality_filter_preserves_original_should_query():
    app.search_service.CONFIG = _with_quality_ranking(discovery_filter_enabled=True)
    payload = app.build_category_discovery_query("toilet paper")
    inner = payload["query"]["bool"]["must"][0]["bool"]
    assert inner["minimum_should_match"] == 1
    assert inner["should"]
