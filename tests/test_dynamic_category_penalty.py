"""
`services/search_service.py` içindeki `_apply_dynamic_category_penalty`
(dinamik kategori keşfinin bulduğu TOP main_category'lerin DIŞINDA kalan
belgelere hafif penaltı) testleri. Gerçek Elastic Cloud'a HİÇBİR istek
atılmaz — `discovered_categories` doğrudan `IntentSignals.debug` içine
enjekte edilir (bkz. tests/test_dynamic_category_discovery.py'deki aynı
"ES aggregation I/O'sunu mock'lama" felsefesi).
"""

import dataclasses

import pytest

import app
from services.intent_service import IntentSignals


def _signals_with_discovery(discovered_categories):
    return IntentSignals(debug={"dynamic_discovery": discovered_categories})


def _with_dynamic_intent(**overrides):
    di = dataclasses.replace(app.search_service.CONFIG.dynamic_intent, **overrides)
    return dataclasses.replace(app.search_service.CONFIG, dynamic_intent=di)


@pytest.fixture(autouse=True)
def _restore_config():
    original = app.search_service.CONFIG
    # field_consensus/popularity_ranking/accessory_penalty kendi function_score
    # sarmalayıcılarını ekliyor; bu dosya yalnızca dynamic_category_penalty'nin
    # kendi wrapper'ına odaklı kalsın diye hepsi burada devre dışı bırakılıyor
    # (bkz. aynı gerekçe test_quality_ranking.py/test_popularity_ranking.py'de).
    app.search_service.CONFIG = dataclasses.replace(
        original,
        field_consensus=dataclasses.replace(original.field_consensus, enabled=False),
        popularity_ranking=dataclasses.replace(original.popularity_ranking, enabled=False),
        accessory_penalty=dataclasses.replace(original.accessory_penalty, enabled=False),
    )
    yield
    app.search_service.CONFIG = original


def _penalty_functions(payload):
    fs = payload["query"]["function_score"]
    return [f for f in fs["functions"] if "filter" in f]


def test_no_op_when_no_main_category_discovered():
    # Yalnızca "categories"/"source_category" alanlarından aday gelmiş,
    # "main_category" hiç yok -- penaltı hiç uygulanmamalı (fail-safe).
    signals = _signals_with_discovery([
        {"value": "Electronics", "field": "categories", "doc_count": 100, "rank": 1, "source": "dynamic_category_discovery"},
    ])
    payload = app.search_service.build_search_query(
        "wireless headphones", intent_signals=signals, apply_intent_reranking=True
    )
    assert "function_score" not in payload["query"]
    assert "bool" in payload["query"]


def test_no_op_when_discovery_empty():
    signals = _signals_with_discovery([])
    payload = app.search_service.build_search_query(
        "wireless headphones", intent_signals=signals, apply_intent_reranking=True
    )
    assert "function_score" not in payload["query"]


def test_wraps_query_with_must_not_terms_for_top_main_categories():
    signals = _signals_with_discovery([
        {"value": "All Electronics", "field": "main_category", "doc_count": 500, "rank": 1, "source": "dynamic_category_discovery"},
        {"value": "Cell Phones & Accessories", "field": "main_category", "doc_count": 200, "rank": 2, "source": "dynamic_category_discovery"},
    ])
    payload = app.search_service.build_search_query(
        "wireless headphones", intent_signals=signals, apply_intent_reranking=True
    )
    assert "function_score" in payload["query"]
    penalty_fns = _penalty_functions(payload)
    assert len(penalty_fns) == 1
    terms = penalty_fns[0]["filter"]["bool"]["must_not"][0]["terms"]["main_category"]
    assert set(terms) == {"All Electronics", "Cell Phones & Accessories"}
    assert penalty_fns[0]["weight"] == app.search_service.CONFIG.dynamic_intent.negative_category_penalty


def test_baseline_weight_keeps_top_category_docs_neutral():
    signals = _signals_with_discovery([
        {"value": "All Electronics", "field": "main_category", "doc_count": 500, "rank": 1, "source": "dynamic_category_discovery"},
    ])
    payload = app.search_service.build_search_query(
        "wireless headphones", intent_signals=signals, apply_intent_reranking=True
    )
    fs = payload["query"]["function_score"]
    assert fs["score_mode"] == "multiply"
    unconditional = [f for f in fs["functions"] if "filter" not in f]
    assert unconditional == [{"weight": 1.0}]


def test_disabled_when_penalty_is_one_or_above():
    app.search_service.CONFIG = _with_dynamic_intent(negative_category_penalty=1.0)
    signals = _signals_with_discovery([
        {"value": "All Electronics", "field": "main_category", "doc_count": 500, "rank": 1, "source": "dynamic_category_discovery"},
    ])
    payload = app.search_service.build_search_query(
        "wireless headphones", intent_signals=signals, apply_intent_reranking=True
    )
    assert "function_score" not in payload["query"]


def test_disabled_when_dynamic_intent_disabled():
    app.search_service.CONFIG = _with_dynamic_intent(enabled=False)
    signals = _signals_with_discovery([
        {"value": "All Electronics", "field": "main_category", "doc_count": 500, "rank": 1, "source": "dynamic_category_discovery"},
    ])
    payload = app.search_service.build_search_query(
        "wireless headphones", intent_signals=signals, apply_intent_reranking=True
    )
    assert "function_score" not in payload["query"]


def test_no_extra_elasticsearch_request_reuses_already_computed_discovery():
    # discovered_categories IntentSignals.debug'dan okunuyor -- build_search_query
    # saf bir fonksiyon olarak kalır, bu fonksiyon içinde HİÇBİR HTTP çağrısı
    # yapılmaz (_post_search çağrılmaz).
    import inspect

    source = inspect.getsource(app.search_service._apply_dynamic_category_penalty)
    code_only = source.split('"""', 2)[-1]
    assert "_post_search" not in code_only
