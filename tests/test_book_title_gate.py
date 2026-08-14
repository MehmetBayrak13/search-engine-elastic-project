"""
`services/search_service.py` içindeki `_book_title_gate_must_not` (kitap/
e-kitap/sesli kitap kategorilerinde sert "neredeyse tam başlık eşleşmesi"
kapısı) testleri. Gerçek Elastic Cloud'a HİÇBİR istek atılmaz.

Motivasyon: "Abuzittin'in Kırmızı Arabası" gibi bir kitap, "kırmızı" ya da
"kırmızı araba" gibi PARÇALI sorgularda da çıkıyordu (kelimeler başlıkta
gerçekten geçtiği için multi_match/fuzzy bunu meşru sayıyordu). Bu dosya,
kitap kategorisindeki ürünlerin YALNIZCA sorgu başlığın (neredeyse) tamamına
eşitse sonuçlarda kalabildiğini doğrular.
"""

import dataclasses

import pytest

import app


@pytest.fixture(autouse=True)
def _restore_config():
    original = app.search_service.CONFIG
    # field_consensus/popularity_ranking/accessory_penalty kendi
    # function_score sarmalayıcılarını ekliyor; bu dosya yalnızca
    # book_title_gate'in kendi must_not maddesine odaklı kalsın diye
    # üçü de devre dışı bırakılıyor (bkz. tests/test_accessory_penalty.py
    # aynı desen).
    app.search_service.CONFIG = dataclasses.replace(
        original,
        field_consensus=dataclasses.replace(original.field_consensus, enabled=False),
        popularity_ranking=dataclasses.replace(original.popularity_ranking, enabled=False),
        accessory_penalty=dataclasses.replace(original.accessory_penalty, enabled=False),
    )
    yield
    app.search_service.CONFIG = original


def _with_book_gate(**overrides):
    gate = dataclasses.replace(app.search_service.CONFIG.book_title_gate, **overrides)
    return dataclasses.replace(app.search_service.CONFIG, book_title_gate=gate)


def _book_gate_clause(payload):
    must_not = payload["query"]["bool"]["must_not"]
    gate_clauses = [c for c in must_not if "filter" in c.get("bool", {})]
    assert len(gate_clauses) == 1, "tam olarak bir book_title_gate maddesi bekleniyor"
    return gate_clauses[0]["bool"]


def test_book_gate_clause_present_by_default():
    payload = app.build_search_query("kırmızı araba", apply_intent_reranking=False)
    gate = _book_gate_clause(payload)
    cfg = app.search_service.CONFIG.book_title_gate
    assert gate["filter"] == [{"terms": {cfg.category_field: list(cfg.categories)}}]


def test_book_gate_excludes_unless_title_fuzzy_matches_or_asin_matches():
    payload = app.build_search_query("kırmızı araba", apply_intent_reranking=False)
    gate = _book_gate_clause(payload)
    inner_should = gate["must_not"][0]["bool"]["should"]
    # `fuzzy` "case_insensitive" desteklemez (ES parse hatası verir) -- bu
    # alan zaten lowercase_normalizer taşıdığı için gerek de yok (bkz.
    # BookTitleGateConfig docstring'i).
    assert inner_should[0] == {
        "fuzzy": {
            "title.keyword": {
                "value": "kırmızı araba",
                "fuzziness": "AUTO",
            }
        }
    }
    assert inner_should[1] == {
        "term": {"parent_asin": {"value": "kırmızı araba", "case_insensitive": True}}
    }
    assert gate["must_not"][0]["bool"]["minimum_should_match"] == 1


def test_book_gate_query_text_flows_through_for_exact_title_query():
    exact_title = "Abuzittin'in Kırmızı Arabası"
    payload = app.build_search_query(exact_title, apply_intent_reranking=False)
    gate = _book_gate_clause(payload)
    fuzzy_clause = gate["must_not"][0]["bool"]["should"][0]["fuzzy"]["title.keyword"]
    assert fuzzy_clause["value"] == exact_title


def test_disabled_produces_no_book_gate_clause():
    app.search_service.CONFIG = _with_book_gate(enabled=False)
    payload = app.build_search_query("kırmızı araba", apply_intent_reranking=False)
    assert "must_not" not in payload["query"]["bool"]


def test_empty_categories_produces_no_book_gate_clause():
    app.search_service.CONFIG = _with_book_gate(categories=())
    payload = app.build_search_query("kırmızı araba", apply_intent_reranking=False)
    assert "must_not" not in payload["query"]["bool"]


def test_book_gate_categories_come_from_config():
    app.search_service.CONFIG = _with_book_gate(categories=("books", "buy a kindle", "audible audiobooks"))
    payload = app.build_search_query("kırmızı araba", apply_intent_reranking=False)
    gate = _book_gate_clause(payload)
    assert gate["filter"][0]["terms"]["main_category"] == ["books", "buy a kindle", "audible audiobooks"]


def test_book_gate_does_not_disable_normal_lexical_matching():
    # Kapı yalnızca EK bir must_not maddesidir -- mevcut zorunlu lexical
    # bool.must grubunu (phrase/multi_match/fuzzy/exact_asin) hiç etkilemez.
    payload = app.build_search_query("kırmızı araba", apply_intent_reranking=False)
    assert payload["query"]["bool"]["must"][0]["bool"]["should"]
