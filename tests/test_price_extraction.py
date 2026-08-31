from dataclasses import replace

from services import autocomplete_service, search_service

from services.search_service import extract_price_constraint


def _innermost_query(query_node):
    node = query_node
    while isinstance(node, dict):
        if "function_score" in node:
            node = node["function_score"]["query"]
        elif "boosting" in node:
            node = node["boosting"]["positive"]
        else:
            break
    return node


def test_extract_price_constraint_recognizes_english_under_patterns():
    assert extract_price_constraint("under $50 headphones") == {"max_price": 50.0}
    assert extract_price_constraint("wireless mouse under 30 dollars") == {"max_price": 30.0}
    assert extract_price_constraint("below $99.99 monitor") == {"max_price": 99.99}
    assert extract_price_constraint("laptop less than $500") == {"max_price": 500.0}


def test_extract_price_constraint_recognizes_english_over_patterns():
    assert extract_price_constraint("over $100 monitor") == {"min_price": 100.0}
    assert extract_price_constraint("headphones above $80") == {"min_price": 80.0}
    assert extract_price_constraint("chair more than $200") == {"min_price": 200.0}


def test_extract_price_constraint_recognizes_turkish_patterns():
    assert extract_price_constraint("50 dolar altı kulaklık") == {"max_price": 50.0}
    assert extract_price_constraint("kulaklık 50 dolardan ucuz") == {"max_price": 50.0}
    assert extract_price_constraint("200 dolar üstü telefon") == {"min_price": 200.0}
    assert extract_price_constraint("100 dolardan fazla saat") == {"min_price": 100.0}


def test_extract_price_constraint_does_not_confuse_bare_numbers_or_units():
    # "unit_matching"in alanı -- fiyat kısıtı DEĞİL, para birimi/karşılaştırma
    # ifadesi olmadığı için tetiklenmemeli.
    assert extract_price_constraint("32 inch monitor") is None
    assert extract_price_constraint("500ml water bottle") is None
    assert extract_price_constraint("wireless mouse") is None
    assert extract_price_constraint("") is None
    assert extract_price_constraint(None) is None


def test_price_constraint_becomes_a_bool_filter_range_clause():
    payload = search_service.build_search_query("under $50 headphones", apply_intent_reranking=False)
    filters = _innermost_query(payload["query"])["bool"].get("filter")
    assert filters == [{"range": {"price": {"lte": 50.0}}}]


def test_price_constraint_min_becomes_gte_filter():
    payload = search_service.build_search_query("over $100 monitor", apply_intent_reranking=False)
    filters = _innermost_query(payload["query"])["bool"].get("filter")
    assert filters == [{"range": {"price": {"gte": 100.0}}}]


def test_no_price_constraint_means_no_filter_key():
    payload = search_service.build_search_query("wireless mouse", apply_intent_reranking=False)
    assert "filter" not in _innermost_query(payload["query"])["bool"]


def test_price_phrase_is_stripped_from_lexical_match_text():
    # Regresyon: fiyat ifadesi ("under $30") gerçek ürün başlıklarında hiç
    # geçmez -- field_relevance.operator "and" altında lexical eşleşmeye
    # sızarsa TÜM eşleşmeleri bozar (canlıda doğrulandı: "under $30
    # wireless mouse" neredeyse hiç sonuç döndürmüyordu). Lexical madde
    # metni TEMİZLENMİŞ ("wireless mouse") olmalı, fiyat filtresi ayrı
    # olarak bool.filter'da kalmalı.
    payload = search_service.build_search_query("under $30 wireless mouse", apply_intent_reranking=False)
    inner = _innermost_query(payload["query"])
    filters = inner["bool"].get("filter")
    assert filters == [{"range": {"price": {"lte": 30.0}}}]

    should = inner["bool"]["must"][0]["bool"]["should"]
    phrase_clause = next(c["match_phrase"]["title"]["query"] for c in should if "match_phrase" in c)
    assert phrase_clause == "wireless mouse"
    assert "under" not in phrase_clause
    assert "30" not in phrase_clause


def test_price_constraint_does_not_gate_lexical_search():
    # Fiyat filtresi bool.filter'da yaşamalı, zorunlu lexical bool.must
    # grubunu hiç etkilememeli.
    payload = search_service.build_search_query("under $50 headphones", apply_intent_reranking=False)
    must = _innermost_query(payload["query"])["bool"]["must"]
    assert len(must) == 1


def test_enable_price_extraction_false_ignores_price_phrase():
    # `enable_price_extraction=False` -- diğer üç yöntem (phrase/multi_match/
    # fuzzy/exact_asin) gibi istek bazlı bir toggle. Kapalıyken hem filtre
    # eklenmemeli hem de fiyat ifadesi lexical metinden ÇIKARILMAMALI (eski
    # davranış: "under"/"$50" olduğu gibi aranır, muhtemelen az sonuç verir
    # -- bu bilerek geri getirilen "kapalı" davranıştır).
    payload = search_service.build_search_query(
        "under $50 headphones", apply_intent_reranking=False, enable_price_extraction=False
    )
    inner = _innermost_query(payload["query"])
    assert "filter" not in inner["bool"]

    should = inner["bool"]["must"][0]["bool"]["should"]
    phrase_clause = next(c["match_phrase"]["title"]["query"] for c in should if "match_phrase" in c)
    assert phrase_clause == "under $50 headphones"


def test_price_extraction_disabled_via_config_ignores_price_phrases(tmp_path_factory):
    from services.search_service import build_search_query
    from config import load_search_config
    import json
    from pathlib import Path

    data = json.loads(Path("config/search_config.json").read_text(encoding="utf-8"))
    data["price_extraction"]["enabled"] = False
    path = tmp_path_factory.mktemp("cfg") / "search_config.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    override_config = load_search_config(path)

    payload = build_search_query(
        "under $50 headphones", config=override_config, apply_intent_reranking=False
    )
    assert "filter" not in _innermost_query(payload["query"])["bool"]
