import app


def test_phrase_translation_kablosuz_kulaklik():
    result = app.expand_multilingual_query("kablosuz kulaklık")
    assert "wireless headphones" in result["phrase_translations"]
    assert result["used_translation"] is True


def test_phrase_translation_oyuncu_faresi():
    result = app.expand_multilingual_query("oyuncu faresi")
    assert "gaming mouse" in result["phrase_translations"]


def test_phrase_translation_tuvalet_kagidi():
    result = app.expand_multilingual_query("tuvalet kağıdı")
    assert "toilet paper" in result["phrase_translations"]


def test_phrase_translation_takes_priority_over_token_translation():
    result = app.expand_multilingual_query("kablosuz kulaklık")
    assert result["phrase_translations"]
    assert result["token_translations"] == []


def test_token_translation_fallback_when_no_phrase_match():
    result = app.expand_multilingual_query("kablosuz")
    assert result["phrase_translations"] == []
    assert "wireless" in result["token_translations"]


def test_unknown_query_falls_back_to_original_only():
    result = app.expand_multilingual_query("wireless headphones")
    assert result["expanded_queries"] == ["wireless headphones"]
    assert result["used_translation"] is False


def test_original_query_is_always_preserved_first():
    result = app.expand_multilingual_query("kablosuz kulaklık")
    assert result["expanded_queries"][0] == "kablosuz kulaklık"


def test_expanded_queries_has_no_duplicates():
    result = app.expand_multilingual_query("kablosuz kulaklık")
    assert len(result["expanded_queries"]) == len(set(result["expanded_queries"]))


def test_max_variant_limit_is_respected():
    result = app.expand_multilingual_query("kablosuz kulaklık")
    assert len(result["phrase_translations"]) <= app.CONFIG.translation.max_variants


def test_empty_query_does_not_crash():
    result = app.expand_multilingual_query("")
    assert result["expanded_queries"] == [""]
    assert result["used_translation"] is False


def test_translation_adds_lexical_alternative_without_bypassing_gate():
    payload = app.build_search_query("kablosuz kulaklık", apply_intent_reranking=False)
    lexical = payload["query"]["bool"]["must"][0]["bool"]["should"]
    # 4 lexical yöntem (asin/phrase/multi/fuzzy) + en az 1 çeviri alternatifi.
    assert len(lexical) >= 5


def test_english_query_without_translation_match_is_unaffected():
    with_translation = app.build_search_query("wireless headphones", apply_intent_reranking=False)
    lexical = with_translation["query"]["bool"]["must"][0]["bool"]["should"]
    # Çeviri sözlüğünde eşleşme yok; asin(1) + phrase(1) + field_relevance
    # (8 alan + 1 cross_fields = 9, eski tekli multi_match'in YERİNE geçti)
    # + fuzzy(1) + title_ranking'in bağımsız 2 katmanı (exact + prefix) = 14,
    # çeviri alternatifi olmamalı.
    assert len(lexical) == 1 + 1 + len(app.CONFIG.field_relevance.fields) + 1 + 1 + 2


def test_autocomplete_translation_only_adds_full_phrase_alternatives():
    payload = app.build_autocomplete_query("kablosuz kulaklık", apply_intent_reranking=False)
    should_group = payload["query"]["bool"]["must"][0]["bool"]["should"]
    assert len(should_group) >= 2  # orijinal + en az bir çevrilmiş ifade


def test_autocomplete_does_not_expand_partial_prefix():
    # "kablo" sözlükte tam terim olarak yok ("kablosuz" var); saldırgan bir
    # genişleme olmamalı.
    payload = app.build_autocomplete_query("kablo", apply_intent_reranking=False)
    should_group = payload["query"]["bool"]["must"][0]["bool"]["should"]
    assert len(should_group) == 1
