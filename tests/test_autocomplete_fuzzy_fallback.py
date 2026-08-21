from dataclasses import replace

from services import autocomplete_service, search_service

def test_autocomplete_adds_fuzzy_fallback_on_plain_title_field():
    payload = autocomplete_service.build_autocomplete_query("keybord", apply_intent_reranking=False)
    should_group = payload["query"]["bool"]["must"][0]["bool"]["should"]
    fallback = next(c["match"]["title"] for c in should_group if "title" in c.get("match", {}))
    assert fallback["query"] == "keybord"
    assert fallback["fuzziness"] == "AUTO"
    assert fallback["boost"] == search_service.CONFIG.search_methods.autocomplete.fuzzy_fallback_boost


def test_autocomplete_fuzzy_fallback_does_not_replace_primary_ngram_match():
    # Birincil (hızlı, kesin) ngram eşleşmesi hâlâ ilk madde olarak kalmalı --
    # fuzzy fallback ONA EK, onun YERİNE değil.
    payload = autocomplete_service.build_autocomplete_query("wireless", apply_intent_reranking=False)
    should_group = payload["query"]["bool"]["must"][0]["bool"]["should"]
    assert should_group[0]["match"]["title.autocomplete"] == {"query": "wireless", "operator": "and"}


def test_autocomplete_fuzzy_fallback_can_be_disabled_via_config(monkeypatch):
    import services.search_service as search_service
    from services.autocomplete_service import build_autocomplete_query

    off_cfg = replace(
        search_service.CONFIG,
        search_methods=replace(
            search_service.CONFIG.search_methods,
            autocomplete=replace(search_service.CONFIG.search_methods.autocomplete, fuzzy_fallback_enabled=False),
        ),
    )
    monkeypatch.setattr(search_service, "CONFIG", off_cfg)
    payload = build_autocomplete_query("keybord", apply_intent_reranking=False)
    should_group = payload["query"]["bool"]["must"][0]["bool"]["should"]
    assert len(should_group) == 1
    assert should_group[0]["match"]["title.autocomplete"]["query"] == "keybord"


def test_autocomplete_fuzzy_fallback_still_requires_the_should_group_to_match():
    # Fallback bool.should'ta yaşar, minimum_should_match:1 zorunluluğunu
    # gevşetmez -- yine de en az bir madde (ngram YA DA fuzzy) eşleşmeli.
    payload = autocomplete_service.build_autocomplete_query("keybord", apply_intent_reranking=False)
    must_group = payload["query"]["bool"]["must"][0]["bool"]
    assert must_group["minimum_should_match"] == 1
