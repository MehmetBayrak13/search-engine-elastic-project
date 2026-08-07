import json
from pathlib import Path

import app
from config import clear_config_cache, load_search_config


def _repo_search_config_dict():
    return json.loads(Path("config/search_config.json").read_text(encoding="utf-8"))


def _tmp_path_config(tmp_path_factory, data):
    path = tmp_path_factory.mktemp("cfg") / "search_config.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_build_search_query_config_override_changes_size_without_touching_global(tmp_path_factory):
    import services.search_service as search_service

    data = _repo_search_config_dict()
    data["limits"]["result_size"] = 3
    data["pagination"]["enabled"] = False
    override_path = _tmp_path_config(tmp_path_factory, data)
    override_config = load_search_config(override_path)

    payload = search_service.build_search_query("wireless mouse", config=override_config)
    assert payload["size"] == 3
    assert search_service.CONFIG.limits.result_size != 3
    clear_config_cache()


def test_no_lexical_methods_returns_match_none():
    from dataclasses import replace

    # title_ranking's exact/prefix tiers are a lexical method in their own
    # right (gated only by config, not by these enable_* switches), so they
    # must also be turned off to exercise the true "no lexical method"
    # safety net.
    no_title_ranking_cfg = replace(
        app.CONFIG, title_ranking=replace(app.CONFIG.title_ranking, enabled=False)
    )
    payload = app.build_search_query(
        "kamera",
        enable_phrase=False,
        enable_multi_match=False,
        enable_fuzzy=False,
        enable_exact_asin=False,
        config=no_title_ranking_cfg,
    )
    assert payload["query"] == {"match_none": {}}


def test_lexical_queries_live_under_bool_must():
    payload = app.build_search_query("kamera", apply_intent_reranking=False)
    must = payload["query"]["bool"]["must"]
    assert len(must) == 1
    assert "should" in must[0]["bool"]
    assert must[0]["bool"]["minimum_should_match"] == 1


def test_intent_boosts_live_under_outer_should():
    payload = app.build_search_query("smartwatch")
    should = payload["query"]["bool"].get("should", [])
    assert should, "watch intent sorgusu için should boostları bekleniyor"
    for clause in should:
        assert "match_phrase" in clause


def test_intent_exclusions_live_under_must_not():
    payload = app.build_search_query("smartwatch")
    must_not = payload["query"]["bool"].get("must_not", [])
    assert must_not, "watch niyeti için kitap dışlaması bekleniyor"


def test_watch_book_query_does_not_exclude_books():
    payload = app.build_search_query("watch book")
    assert not payload["query"]["bool"].get("must_not")


def test_exact_asin_field_and_boost_come_from_config():
    payload = app.build_search_query("B000123456", apply_intent_reranking=False)
    lexical = payload["query"]["bool"]["must"][0]["bool"]["should"]
    field = app.CONFIG.search_methods.exact_asin.field
    boost = app.CONFIG.search_methods.exact_asin.boost
    # `term` clauses also come from the independent title_ranking exact tier
    # (title.keyword), so filter for the exact-ASIN field specifically
    # rather than assuming it's the only `term` clause present.
    asin_clauses = [c for c in lexical if "term" in c and field in c["term"]]
    assert len(asin_clauses) == 1
    assert asin_clauses[0]["term"][field]["boost"] == boost


def test_fuzzy_switch_can_be_disabled():
    with_fuzzy = app.build_search_query("kamera", apply_intent_reranking=False)
    without_fuzzy = app.build_search_query(
        "kamera", enable_fuzzy=False, apply_intent_reranking=False
    )
    with_count = len(with_fuzzy["query"]["bool"]["must"][0]["bool"]["should"])
    without_count = len(without_fuzzy["query"]["bool"]["must"][0]["bool"]["should"])
    assert without_count == with_count - 1


def test_field_relevance_produces_one_match_clause_per_configured_field():
    payload = app.build_search_query(
        "kamera",
        enable_phrase=False,
        enable_fuzzy=False,
        enable_exact_asin=False,
        apply_intent_reranking=False,
    )
    should = payload["query"]["bool"]["must"][0]["bool"]["should"]
    match_clauses = [c["match"] for c in should if "match" in c]
    matched_field_names = {name for clause in match_clauses for name in clause}
    for entry in app.CONFIG.field_relevance.fields:
        assert entry.field in matched_field_names
    title_clause = next(c["title"] for c in match_clauses if "title" in c)
    title_entry = next(e for e in app.CONFIG.field_relevance.fields if e.field == "title")
    assert title_clause["boost"] == title_entry.boost
    assert title_clause["operator"] == app.CONFIG.field_relevance.operator


def test_field_relevance_adds_cross_fields_clause():
    payload = app.build_search_query(
        "kamera", enable_phrase=False, enable_fuzzy=False, enable_exact_asin=False, apply_intent_reranking=False,
    )
    should = payload["query"]["bool"]["must"][0]["bool"]["should"]
    cross_fields = [c["multi_match"] for c in should if c.get("multi_match", {}).get("type") == "cross_fields"]
    assert len(cross_fields) == 1
    assert cross_fields[0]["operator"] == app.CONFIG.field_relevance.operator
    assert cross_fields[0]["boost"] == app.CONFIG.field_relevance.cross_fields_boost


def test_field_relevance_disabled_by_enable_multi_match_toggle():
    from dataclasses import replace

    # title_ranking's tiers are an independent lexical method (gated only by
    # config, not by enable_multi_match — see its own tests below), so
    # disable it here to isolate field_relevance's contribution to the
    # should-clause count. Same pattern as
    # test_no_lexical_methods_returns_match_none / test_category_signal_alone_cannot_produce_a_hit.
    # exact_asin is kept ON (identical in both calls) as a stable baseline
    # clause so turning field_relevance off doesn't trip the unrelated
    # "zero lexical methods -> match_none" safety net, which would remove
    # the `bool` key this test asserts on.
    no_title_ranking_cfg = replace(
        app.CONFIG, title_ranking=replace(app.CONFIG.title_ranking, enabled=False)
    )
    with_it = app.build_search_query(
        "kamera", enable_phrase=False, enable_fuzzy=False,
        apply_intent_reranking=False, config=no_title_ranking_cfg,
    )
    without_it = app.build_search_query(
        "kamera", enable_phrase=False, enable_multi_match=False, enable_fuzzy=False,
        apply_intent_reranking=False, config=no_title_ranking_cfg,
    )
    with_count = len(with_it["query"]["bool"]["must"][0]["bool"]["should"])
    without_count = len(without_it["query"]["bool"]["must"][0]["bool"]["should"])
    assert without_count == 1  # exact_asin only
    assert with_count == without_count + len(app.CONFIG.field_relevance.fields) + 1


def test_field_relevance_canonical_field_matches_share_one_canonical_key():
    from services.search_service import _build_field_evidence_clauses

    grouped = _build_field_evidence_clauses("kamera", app.CONFIG)
    assert "title" in grouped
    assert "title.tr" not in grouped
    assert len(grouped["title"]) == 2  # one clause for `title`, one for `title.tr`


def test_field_relevance_debug_names_present_only_when_requested():
    without_debug = app.build_search_query("kamera", apply_intent_reranking=False)
    with_debug = app.build_search_query("kamera", apply_intent_reranking=False, include_relevance_debug=True)

    def _has_any_name(payload):
        should = payload["query"]["bool"]["must"][0]["bool"]["should"]
        return any("_name" in c.get("match", {}).get(f, {}) for c in should for f in c.get("match", {}))

    assert not _has_any_name(without_debug)
    assert _has_any_name(with_debug)


def test_autocomplete_uses_configured_field_and_operator():
    payload = app.build_autocomplete_query("kam", apply_intent_reranking=False)
    inner = payload["query"]["bool"]["must"][0]["bool"]["should"][0]["match"]
    field = app.CONFIG.search_methods.autocomplete.field
    assert field in inner
    assert inner[field]["operator"] == app.CONFIG.search_methods.autocomplete.operator


def test_autocomplete_result_size_defaults_from_config():
    payload = app.build_autocomplete_query("kam")
    assert payload["size"] == app.CONFIG.limits.autocomplete_fetch_size


def test_autocomplete_should_includes_manual_positive_categories_from_new_schema():
    """`iphone_case` kuralının obje-biçimli `positive_categories` alanı (Task 1
    şeması), `category_boost_terms`'ün her zaman yaptığı gibi autocomplete
    sorgusunun dış bool.should'una da yansımalı — bu, autocomplete_service.py
    artık intent_service.resolve_intent_signals + search_service._positive_category_should_clauses
    üzerinden geçtiği için kasıtlı ve kilitlenmesi gereken bir davranıştır
    (bkz. Task 4/5 review fix: önceden hem normal aramada hem autocomplete'te
    ölü/kullanılmayan bir alandı)."""
    payload = app.build_autocomplete_query("iphone case")
    should = payload["query"]["bool"]["should"]

    cases_clauses = [
        clause
        for clause in should
        if "match_phrase" in clause
        and any(body.get("query") == "Cases" for body in clause["match_phrase"].values())
    ]
    fields_hit = {list(clause["match_phrase"].keys())[0] for clause in cases_clauses}
    assert {"categories_text", "categories_text.tr"} <= fields_hit

    # Soft negatif (`Cell Phones`, penalty 0.5) autocomplete'te kasıtlı olarak
    # yok sayılır — autocomplete'in sorgu şekli düz `bool` kalır, `boosting`
    # sarmalayıcısı YOKTUR (dinamik keşif gibi bilerek dahil edilmeyen bir
    # özellik; bkz. build_autocomplete_query docstring'i).
    assert "boosting" not in payload["query"]


def test_detect_search_intent_watch():
    info = app.detect_search_intent("erkek kol saati")
    assert info["intent"] == "watch"
    assert info["apply_exclusion"] is True


def test_detect_search_intent_none_for_unrelated_query():
    info = app.detect_search_intent("wireless headphones")
    assert info["intent"] is None


def test_search_service_resolve_intent_signals_returns_intent_signals_object():
    import services.search_service as search_service
    from services.intent_service import IntentSignals

    signals = search_service.resolve_intent_signals("smartwatch", include_dynamic=False)
    assert isinstance(signals, IntentSignals)
    assert "watch" in signals.matched_rule_ids
    assert signals.legacy_hard_exclusions  # watch->books legacy exclusion still present


def test_category_signal_alone_cannot_produce_a_hit():
    from dataclasses import replace

    # As above: title_ranking's tiers are an independent lexical method, so
    # they must be disabled too in order to prove category signals alone
    # (with genuinely zero lexical methods) cannot produce a hit.
    no_title_ranking_cfg = replace(
        app.CONFIG, title_ranking=replace(app.CONFIG.title_ranking, enabled=False)
    )
    payload = app.build_search_query(
        "wireless mouse",
        enable_phrase=False, enable_multi_match=False, enable_fuzzy=False, enable_exact_asin=False,
        config=no_title_ranking_cfg,
    )
    assert payload["query"] == {"match_none": {}}


def test_object_negative_category_uses_boosting_not_must_not():
    from services.intent_service import IntentSignals
    import services.search_service as search_service

    signals = IntentSignals(
        negative_categories=({"value": "Cell Phones", "penalty": 0.5, "source": "manual"},),
        matched_rule_ids=("iphone_case",),
    )
    payload = search_service.build_search_query("iphone case", intent_signals=signals)
    query = payload["query"]
    assert "boosting" in query
    assert query["boosting"]["negative_boost"] >= search_service.CONFIG.intent_ranking.negative_penalty_floor
    assert "must_not" not in query["boosting"]["positive"].get("bool", {})


def test_legacy_hard_exclusion_still_uses_must_not():
    from services.intent_service import IntentSignals
    import services.search_service as search_service

    signals = IntentSignals(
        legacy_hard_exclusions=({"bool": {"should": [{"term": {"categories": "books"}}], "minimum_should_match": 1}},),
        matched_rule_ids=("watch",),
    )
    payload = search_service.build_search_query("smartwatch", intent_signals=signals)
    assert "boosting" not in payload["query"]
    assert payload["query"]["bool"]["must_not"]


def test_manual_positive_category_renders_categories_text_and_tr():
    from services.intent_service import IntentSignals
    import services.search_service as search_service

    signals = IntentSignals(
        positive_categories=({"value": "Cases", "boost": 3.0, "source": "manual", "field": "categories_text"},),
        matched_rule_ids=("iphone_case",),
    )
    payload = search_service.build_search_query("iphone case", intent_signals=signals)
    should = payload["query"]["bool"]["should"]
    fields_hit = {list(c["match_phrase"].keys())[0] for c in should if "match_phrase" in c}
    assert {"categories_text", "categories_text.tr"} <= fields_hit


def test_legacy_manual_positive_category_renders_distinct_text_and_tr_boosts():
    """Final-review fix (Finding 1): a legacy `category_boost_terms` entry
    (carrying an explicit `boost_tr`) must render DIFFERENT boost values on
    `categories_text` (12) and `categories_text.tr` (8) — restoring
    pre-branch `watch`-rule behavior. Contrast with
    `test_manual_positive_category_renders_categories_text_and_tr` above,
    where the entry has no `boost_tr` key (new `positive_categories`
    schema) and both fields correctly get the SAME boost."""
    from services.intent_service import IntentSignals
    import services.search_service as search_service

    signals = IntentSignals(
        positive_categories=(
            {"value": "watches", "boost": 12, "boost_tr": 8, "source": "manual", "field": "categories_text"},
        ),
        matched_rule_ids=("watch",),
    )
    payload = search_service.build_search_query("smartwatch", intent_signals=signals)
    should = payload["query"]["bool"]["should"]
    boosts = {
        list(c["match_phrase"].keys())[0]: list(c["match_phrase"].values())[0]["boost"]
        for c in should
        if "match_phrase" in c
    }
    assert boosts["categories_text"] == 12
    assert boosts["categories_text.tr"] == 8


def test_dynamic_positive_category_renders_term_on_its_own_field():
    from services.intent_service import IntentSignals
    import services.search_service as search_service

    signals = IntentSignals(
        positive_categories=({"value": "Computers", "boost": 2.0, "source": "dynamic", "field": "main_category"},),
    )
    payload = search_service.build_search_query("laptop", intent_signals=signals)
    should = payload["query"]["bool"]["should"]
    assert any(c.get("term", {}).get("main_category", {}).get("value") == "Computers" for c in should)


def test_title_ranking_adds_exact_and_prefix_tiers():
    payload = app.build_search_query("wireless mouse")
    should = payload["query"]["boosting"]["positive"]["bool"]["must"][0]["bool"]["should"] \
        if "boosting" in payload["query"] else payload["query"]["bool"]["must"][0]["bool"]["should"]
    exact_field = app.CONFIG.title_ranking.exact_field
    assert any(c.get("term", {}).get(exact_field, {}).get("boost") == app.CONFIG.title_ranking.exact_boost for c in should)
    assert any(
        c.get("match_phrase_prefix", {}).get("title", {}).get("boost") == app.CONFIG.title_ranking.prefix_boost
        for c in should
    )
    prefix_clause = next(c for c in should if "match_phrase_prefix" in c)
    assert prefix_clause["match_phrase_prefix"]["title"]["max_expansions"] == app.CONFIG.title_ranking.prefix_max_expansions


def test_title_ranking_tiers_present_even_when_phrase_search_disabled():
    payload = app.build_search_query("wireless mouse", enable_phrase=False)
    should = payload["query"]["bool"]["must"][0]["bool"]["should"]
    exact_field = app.CONFIG.title_ranking.exact_field
    assert any(exact_field in c.get("term", {}) for c in should)
    assert any("match_phrase_prefix" in c for c in should)
    assert not any("match_phrase" in c and c.get("match_phrase", {}).get("title") for c in should)


def test_title_ranking_disabled_omits_new_tiers(monkeypatch):
    import services.search_service as search_service
    from dataclasses import replace

    disabled_cfg = replace(search_service.CONFIG, title_ranking=replace(search_service.CONFIG.title_ranking, enabled=False))
    payload = search_service.build_search_query("wireless mouse", config=disabled_cfg)
    should = payload["query"]["bool"]["must"][0]["bool"]["should"] if "bool" in payload["query"] else \
        payload["query"]["boosting"]["positive"]["bool"]["must"][0]["bool"]["should"]
    exact_field = search_service.CONFIG.title_ranking.exact_field
    assert not any(exact_field in c.get("term", {}) for c in should)
    assert not any("match_phrase_prefix" in c for c in should)
