"""
`services/search_service.py` içindeki `_apply_accessory_penalty` (sorgudan
bağımsız, genel aksesuar/yedek-parça cezası) testleri. Gerçek Elastic
Cloud'a HİÇBİR istek atılmaz.
"""

import dataclasses

import pytest

import app


@pytest.fixture(autouse=True)
def _restore_config():
    original = app.search_service.CONFIG
    # field_consensus/popularity_ranking kendi function_score
    # sarmalayıcılarını ekliyor; bu dosya yalnızca accessory_penalty'nin
    # kendi wrapper'ına odaklı kalsın diye ikisi de devre dışı bırakılıyor.
    app.search_service.CONFIG = dataclasses.replace(
        original,
        field_consensus=dataclasses.replace(original.field_consensus, enabled=False),
        popularity_ranking=dataclasses.replace(original.popularity_ranking, enabled=False),
    )
    yield
    app.search_service.CONFIG = original


def _with_accessory_penalty(**overrides):
    ap = dataclasses.replace(app.search_service.CONFIG.accessory_penalty, **overrides)
    return dataclasses.replace(app.search_service.CONFIG, accessory_penalty=ap)


def _penalty_functions(payload):
    fs = payload["query"]["function_score"]
    return [f for f in fs["functions"] if "filter" in f]


def test_penalizes_accessory_term_not_present_in_query():
    payload = app.build_search_query("wireless headphones", apply_intent_reranking=False)
    assert "function_score" in payload["query"]
    penalty_fns = _penalty_functions(payload)
    assert len(penalty_fns) == 1
    should = penalty_fns[0]["filter"]["bool"]["should"]
    matched_titles_terms = {c["match_phrase"]["title"] for c in should}
    assert "sticker" in matched_titles_terms
    assert "case" in matched_titles_terms
    ratio = app.search_service.CONFIG.accessory_penalty.penalty
    assert penalty_fns[0]["weight"] == ratio


def test_does_not_penalize_term_the_user_explicitly_searched_for():
    # Kullanıcı "phone case" ARADIYSA, "case" terimi sorguda zaten var --
    # bu terim için ceza filtresine dahil EDİLMEMELİ.
    payload = app.build_search_query("phone case", apply_intent_reranking=False)
    penalty_fns = _penalty_functions(payload)
    if penalty_fns:
        should = penalty_fns[0]["filter"]["bool"]["should"]
        matched_terms = {c["match_phrase"]["title"] for c in should}
        assert "case" not in matched_terms
    # "case" tüm terim listesinden çıkarılmış olsa bile diğer terimler
    # (sticker, decal, vb.) hâlâ uygulanabilir olabilir -- crash olmamalı.


def test_baseline_weight_keeps_non_accessory_docs_neutral():
    payload = app.build_search_query("wireless headphones", apply_intent_reranking=False)
    fs = payload["query"]["function_score"]
    assert fs["score_mode"] == "multiply"
    unconditional = [f for f in fs["functions"] if "filter" not in f]
    assert unconditional == [{"weight": 1.0}]


def test_disabled_leaves_query_unwrapped():
    app.search_service.CONFIG = _with_accessory_penalty(enabled=False)
    payload = app.build_search_query("wireless headphones", apply_intent_reranking=False)
    assert "function_score" not in payload["query"]
    assert "bool" in payload["query"]


def test_empty_terms_list_leaves_query_unwrapped():
    app.search_service.CONFIG = _with_accessory_penalty(terms=())
    payload = app.build_search_query("wireless headphones", apply_intent_reranking=False)
    assert "function_score" not in payload["query"]


def test_query_containing_every_configured_term_leaves_query_unwrapped():
    # Sorgu, yapılandırılmış TÜM aksesuar terimlerini zaten içeriyorsa
    # (ör. testin kendi minimal terim listesi ["sticker","case"]dı) ceza
    # filtresi için hiçbir "uygulanabilir terim" kalmaz -- wrapper hiç
    # eklenmemeli.
    app.search_service.CONFIG = _with_accessory_penalty(terms=("sticker",))
    payload = app.build_search_query("phone sticker", apply_intent_reranking=False)
    assert "function_score" not in payload["query"]
