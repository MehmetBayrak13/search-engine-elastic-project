"""
`services/search_service.py` içindeki `_apply_popularity_ranking` (rating_number
tabanlı popülerlik/güven sinyali) entegrasyonu testleri. Gerçek Elastic Cloud'a
HİÇBİR istek atılmaz.
"""

import dataclasses

import pytest

import app


def _with_popularity_ranking(**overrides):
    """`search_service.CONFIG.popularity_ranking`ı geçici olarak override eden
    bir AppConfig döner (bkz. tests/test_quality_ranking.py'deki aynı desen —
    `build_search_query` `app.search_service.CONFIG`'i okur, `app.CONFIG`'i
    değil)."""
    pr = dataclasses.replace(app.search_service.CONFIG.popularity_ranking, **overrides)
    return dataclasses.replace(app.search_service.CONFIG, popularity_ranking=pr)


@pytest.fixture(autouse=True)
def _restore_config():
    original = app.search_service.CONFIG
    # field_consensus/quality_ranking/accessory_penalty kendi function_score
    # sarmalayıcılarını ekliyor; bu dosyanın "en dıştaki wrapper
    # popularity_ranking'e mi ait" doğrulamaları bulanıklaşmasın diye burada
    # devre dışı bırakılıyor (bkz. aynı gerekçe test_quality_ranking.py'de).
    app.search_service.CONFIG = dataclasses.replace(
        original,
        field_consensus=dataclasses.replace(original.field_consensus, enabled=False),
        accessory_penalty=dataclasses.replace(original.accessory_penalty, enabled=False),
    )
    yield
    app.search_service.CONFIG = original


def test_popularity_ranking_enabled_by_default():
    assert app.search_service.CONFIG.popularity_ranking.enabled is True


def test_popularity_ranking_wraps_query_in_multiplicative_function_score():
    payload = app.build_search_query("wireless headphones", apply_intent_reranking=False)
    assert "function_score" in payload["query"]
    fs = payload["query"]["function_score"]
    assert fs["boost_mode"] == "multiply"
    assert fs["score_mode"] == "sum"
    weight_fns = [f for f in fs["functions"] if "weight" in f]
    factor_fns = [f for f in fs["functions"] if "field_value_factor" in f]
    assert weight_fns[0]["weight"] == app.CONFIG.popularity_ranking.baseline
    assert factor_fns[0]["field_value_factor"]["field"] == "rating_number"
    assert factor_fns[0]["field_value_factor"]["modifier"] == "log1p"
    assert factor_fns[0]["field_value_factor"]["missing"] == 0


def test_popularity_ranking_never_zeroes_score_for_unrated_products():
    # weight: baseline HER belgede koşulsuz uygulanır (filtresiz) -- bu yüzden
    # rating_number=0/eksik olan bir belgenin toplam çarpanı asla baseline'ın
    # altına inmez (field_value_factor tek başına olsaydı log1p(0)=0 ile
    # skoru sıfırlardı).
    payload = app.build_search_query("wireless headphones", apply_intent_reranking=False)
    fs = payload["query"]["function_score"]
    weight_fns = [f for f in fs["functions"] if "weight" in f]
    assert len(weight_fns) == 1
    assert "filter" not in weight_fns[0]


def test_popularity_ranking_disabled_leaves_query_unwrapped():
    app.search_service.CONFIG = _with_popularity_ranking(enabled=False)
    payload = app.build_search_query("wireless headphones", apply_intent_reranking=False)
    assert "function_score" not in payload["query"]
    assert "bool" in payload["query"]


def test_popularity_ranking_uses_configured_factor():
    app.search_service.CONFIG = _with_popularity_ranking(factor=7.5)
    payload = app.build_search_query("wireless headphones", apply_intent_reranking=False)
    fs = payload["query"]["function_score"]
    factor_fns = [f for f in fs["functions"] if "field_value_factor" in f]
    assert factor_fns[0]["field_value_factor"]["factor"] == 7.5


def test_popularity_ranking_is_outermost_wrapper_around_quality_ranking():
    # Her ikisi de boost_mode: multiply kullanıyor, ama popularity_ranking
    # quality_ranking'in DIŞINDA sarmalanmalı — böylece kalite penaltı/boost'u
    # önce uygulanır, popülerlik çarpanı en son (nihai) skoru ölçekler.
    original = app.search_service.CONFIG
    app.search_service.CONFIG = dataclasses.replace(
        original,
        field_consensus=dataclasses.replace(original.field_consensus, enabled=False),
        quality_ranking=dataclasses.replace(original.quality_ranking, enabled=True),
    )
    payload = app.build_search_query("wireless headphones", apply_intent_reranking=False)
    outer = payload["query"]["function_score"]
    # popularity_ranking'in kendi baseline `weight`i filtresizdir (koşulsuz);
    # quality_ranking'in penaltı `weight`i her zaman bir `filter` taşır (bkz.
    # `_build_quality_functions`), bu yüzden "filtresiz weight" testi ikisini
    # ayırt etmek için güvenilir.
    assert any("weight" in f and "filter" not in f for f in outer["functions"])
    inner = outer["query"]["function_score"]
    assert inner["boost_mode"] == "multiply"
    assert not any("weight" in f and "filter" not in f for f in inner["functions"])
