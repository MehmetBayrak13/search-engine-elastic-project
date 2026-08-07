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


def _innermost_query(query_node):
    """Test helper: `build_search_query` now wraps the base `{"bool": ...}`
    query in a `field_consensus` `function_score` (Task 3) — and optionally
    a `boosting` (soft-negative categories) and/or another `function_score`
    (`quality_ranking`) on top of that. Peels back those wrappers to reach
    the actual `bool` query node so structural assertions written before
    Task 3 keep checking real behavior instead of failing on `KeyError`
    (or, worse, silently passing on a `.get(..., {})` default)."""
    node = query_node
    while isinstance(node, dict):
        if "function_score" in node:
            node = node["function_score"]["query"]
        elif "boosting" in node:
            node = node["boosting"]["positive"]
        else:
            break
    return node


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
    must = _innermost_query(payload["query"])["bool"]["must"]
    assert len(must) == 1
    assert "should" in must[0]["bool"]
    assert must[0]["bool"]["minimum_should_match"] == 1


def test_intent_boosts_live_under_outer_should():
    payload = app.build_search_query("smartwatch")
    should = _innermost_query(payload["query"])["bool"].get("should", [])
    assert should, "watch intent sorgusu için should boostları bekleniyor"
    # Category boosts (from watch intent) are match_phrase, but store_boost is a match clause
    category_clauses = [c for c in should if "match_phrase" in c]
    assert category_clauses, "watch intent category boosts should be present as match_phrase"
    for clause in category_clauses:
        assert "match_phrase" in clause


def test_intent_exclusions_live_under_must_not():
    payload = app.build_search_query("smartwatch")
    must_not = _innermost_query(payload["query"])["bool"].get("must_not", [])
    assert must_not, "watch niyeti için kitap dışlaması bekleniyor"


def test_watch_book_query_does_not_exclude_books():
    payload = app.build_search_query("watch book")
    assert not _innermost_query(payload["query"])["bool"].get("must_not")


def test_exact_asin_field_and_boost_come_from_config():
    payload = app.build_search_query("B000123456", apply_intent_reranking=False)
    lexical = _innermost_query(payload["query"])["bool"]["must"][0]["bool"]["should"]
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
    with_count = len(_innermost_query(with_fuzzy["query"])["bool"]["must"][0]["bool"]["should"])
    without_count = len(_innermost_query(without_fuzzy["query"])["bool"]["must"][0]["bool"]["should"])
    assert without_count == with_count - 1


def test_field_relevance_produces_one_match_clause_per_configured_field():
    payload = app.build_search_query(
        "kamera",
        enable_phrase=False,
        enable_fuzzy=False,
        enable_exact_asin=False,
        apply_intent_reranking=False,
    )
    should = _innermost_query(payload["query"])["bool"]["must"][0]["bool"]["should"]
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
    should = _innermost_query(payload["query"])["bool"]["must"][0]["bool"]["should"]
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
    with_count = len(_innermost_query(with_it["query"])["bool"]["must"][0]["bool"]["should"])
    without_count = len(_innermost_query(without_it["query"])["bool"]["must"][0]["bool"]["should"])
    assert without_count == 1  # exact_asin only
    assert with_count == without_count + len(app.CONFIG.field_relevance.fields) + 1


def test_token_translation_only_query_does_not_crash_and_uses_field_relevance_fields():
    """Regression test for a fix bundled with this task: `"kablosuz"` alone has
    no phrase-level translation entry in config/query_translations.json (only
    a token-level one, "kablosuz" -> "wireless"), so it exercises ONLY the
    token-translation branch of `_build_translation_lexical_queries`, which
    used to read the now-removed `CONFIG.search_methods.multi_match.es_fields`
    and crash with AttributeError. It was fixed to read
    `CONFIG.field_relevance.es_fields` instead, but had no covering test —
    this locks that fix in so a future refactor of `field_relevance.es_fields`
    can't silently reintroduce the crash with the suite still green."""
    expansion = app.expand_multilingual_query("kablosuz")
    assert expansion["phrase_translations"] == []
    assert expansion["token_translations"] == ["wireless"]

    payload = app.build_search_query("kablosuz", apply_intent_reranking=False)

    should = _innermost_query(payload["query"])["bool"]["must"][0]["bool"]["should"]
    token_translation_clauses = [
        c["multi_match"] for c in should
        if c.get("multi_match", {}).get("query") == "wireless"
    ]
    assert len(token_translation_clauses) == 1
    assert token_translation_clauses[0]["fields"] == app.CONFIG.field_relevance.es_fields


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
        should = _innermost_query(payload["query"])["bool"]["must"][0]["bool"]["should"]
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
    assert "must_not" not in _innermost_query(query["boosting"]["positive"]).get("bool", {})


def test_legacy_hard_exclusion_still_uses_must_not():
    from services.intent_service import IntentSignals
    import services.search_service as search_service

    signals = IntentSignals(
        legacy_hard_exclusions=({"bool": {"should": [{"term": {"categories": "books"}}], "minimum_should_match": 1}},),
        matched_rule_ids=("watch",),
    )
    payload = search_service.build_search_query("smartwatch", intent_signals=signals)
    assert "boosting" not in payload["query"]
    assert _innermost_query(payload["query"])["bool"]["must_not"]


def test_manual_positive_category_renders_categories_text_and_tr():
    from services.intent_service import IntentSignals
    import services.search_service as search_service

    signals = IntentSignals(
        positive_categories=({"value": "Cases", "boost": 3.0, "source": "manual", "field": "categories_text"},),
        matched_rule_ids=("iphone_case",),
    )
    payload = search_service.build_search_query("iphone case", intent_signals=signals)
    should = _innermost_query(payload["query"])["bool"]["should"]
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
    should = _innermost_query(payload["query"])["bool"]["should"]
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
    should = _innermost_query(payload["query"])["bool"]["should"]
    assert any(c.get("term", {}).get("main_category", {}).get("value") == "Computers" for c in should)


def test_title_ranking_adds_exact_and_prefix_tiers():
    payload = app.build_search_query("wireless mouse")
    should = _innermost_query(payload["query"])["bool"]["must"][0]["bool"]["should"]
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
    should = _innermost_query(payload["query"])["bool"]["must"][0]["bool"]["should"]
    exact_field = app.CONFIG.title_ranking.exact_field
    assert any(exact_field in c.get("term", {}) for c in should)
    assert any("match_phrase_prefix" in c for c in should)
    assert not any("match_phrase" in c and c.get("match_phrase", {}).get("title") for c in should)


def test_title_ranking_disabled_omits_new_tiers(monkeypatch):
    import services.search_service as search_service
    from dataclasses import replace

    disabled_cfg = replace(search_service.CONFIG, title_ranking=replace(search_service.CONFIG.title_ranking, enabled=False))
    payload = search_service.build_search_query("wireless mouse", config=disabled_cfg)
    should = _innermost_query(payload["query"])["bool"]["must"][0]["bool"]["should"]
    exact_field = search_service.CONFIG.title_ranking.exact_field
    assert not any(exact_field in c.get("term", {}) for c in should)
    assert not any("match_phrase_prefix" in c for c in should)


def _find_function_score_by_name_prefix(query_node, name_prefix):
    """Test helper: walks a query dict looking for a function_score whose
    first function's filter carries a `_name` starting with `name_prefix`.
    Returns the function_score dict or None."""
    if not isinstance(query_node, dict):
        return None
    if "function_score" in query_node:
        fs = query_node["function_score"]
        for fn in fs.get("functions", []):
            name = fn.get("filter", {}).get("bool", {}).get("_name", "")
            if name.startswith(name_prefix):
                return fs
        found = _find_function_score_by_name_prefix(fs.get("query"), name_prefix)
        if found:
            return found
    for key in ("bool", "boosting"):
        if key in query_node:
            inner = query_node[key]
            if isinstance(inner, dict):
                for sub_key in ("query", "positive"):
                    if sub_key in inner:
                        found = _find_function_score_by_name_prefix(inner[sub_key], name_prefix)
                        if found:
                            return found
    return None


def test_field_consensus_wraps_query_in_function_score():
    payload = app.build_search_query("kamera", include_relevance_debug=True)
    fs = _find_function_score_by_name_prefix(payload["query"], "consensus:")
    assert fs is not None
    assert fs["score_mode"] == "max"
    assert fs["boost_mode"] == "multiply"
    assert len(fs["functions"]) == 3


def test_field_consensus_filters_never_use_exists():
    payload = app.build_search_query("kamera", include_relevance_debug=True)
    raw = json.dumps(payload)
    fs = _find_function_score_by_name_prefix(payload["query"], "consensus:")
    assert "exists" not in json.dumps(fs)


def test_field_consensus_tier_weights_come_from_config():
    payload = app.build_search_query("kamera", include_relevance_debug=True)
    fs = _find_function_score_by_name_prefix(payload["query"], "consensus:")
    weights = sorted(fn["weight"] for fn in fs["functions"])
    fc = app.CONFIG.field_consensus
    assert weights == sorted([fc.two_field_boost, fc.three_field_boost, fc.four_plus_field_boost])


def test_field_consensus_disabled_removes_wrapper(tmp_path_factory):
    import services.search_service as search_service
    from config import load_search_config

    data = _repo_search_config_dict()
    data["field_consensus"]["enabled"] = False
    override_path = _tmp_path_config(tmp_path_factory, data)
    override_config = load_search_config(override_path)

    payload = search_service.build_search_query("kamera", config=override_config, include_relevance_debug=True)
    assert _find_function_score_by_name_prefix(payload["query"], "consensus:") is None
    clear_config_cache()


def test_relevance_contradiction_wraps_query_when_rule_matches():
    payload = app.build_search_query("men perfume", include_relevance_debug=True)
    fs = _find_function_score_by_name_prefix(payload["query"], "contradiction")
    # contradiction filters don't carry a top-level "consensus:"/"contradiction:" bool _name
    # the way consensus does; assert via the per-field clause names instead.
    raw = json.dumps(payload)
    assert "contradiction:description" in raw
    assert "contradiction:features" in raw
    assert "contradiction:categories_text" in raw


def test_relevance_contradiction_never_produces_must_not():
    payload = app.build_search_query("men perfume")
    # payload["query"] is always a function_score wrapper (Task 3's field_consensus
    # wraps unconditionally when enabled) — peel back to the real bool node, same
    # as every other structural assertion in this file (see _innermost_query).
    # must_not, when present, is reserved for legacy_hard_exclusions only (watch/book) —
    # this query has no watch/book signal, so must_not must be entirely absent.
    assert "must_not" not in _innermost_query(payload["query"])["bool"]


def test_relevance_contradiction_absent_without_contradiction_terms():
    payload = app.build_search_query("wireless mouse", include_relevance_debug=True)
    raw = json.dumps(payload)
    assert "contradiction:" not in raw


def test_relevance_contradiction_score_mode_is_min_not_max():
    payload = app.build_search_query("men perfume", include_relevance_debug=True)
    query = payload["query"]
    # Walk to the contradiction function_score specifically (it wraps the consensus one).
    fs = query["function_score"]  # consensus is innermost only if contradiction wraps it next
    contradiction_fs = fs if any(
        "contradiction:" in json.dumps(fn.get("filter", {})) for fn in fs.get("functions", [])
    ) else None
    if contradiction_fs is None:
        contradiction_fs = query  # fallback shape check below will fail loudly if structure differs
    assert contradiction_fs["score_mode"] == "min"
    assert contradiction_fs["boost_mode"] == "multiply"


def test_relevance_contradiction_tiers_are_named_for_debug():
    payload = app.build_search_query("men perfume", include_relevance_debug=True)
    raw = json.dumps(payload)
    assert "contradiction_tier:mild" in raw
    assert "contradiction_tier:strong" in raw


def test_relevance_contradiction_tier_names_absent_without_debug():
    payload = app.build_search_query("men perfume")
    raw = json.dumps(payload)
    assert "contradiction_tier:" not in raw


def test_store_boost_clause_present_in_outer_should():
    payload = app.build_search_query("sony")
    should = _innermost_query(payload["query"])["bool"].get("should", [])
    store_clauses = [c for c in should if "match" in c and "store" in c["match"]]
    assert len(store_clauses) == 1
    assert store_clauses[0]["match"]["store"]["boost"] == app.CONFIG.field_relevance.store_boost


def test_store_boost_zero_omits_clause(tmp_path_factory):
    import services.search_service as search_service
    from config import load_search_config

    data = _repo_search_config_dict()
    data["field_relevance"]["store_boost"] = 0
    override_path = _tmp_path_config(tmp_path_factory, data)
    override_config = load_search_config(override_path)

    payload = search_service.build_search_query("sony", config=override_config)
    should = _innermost_query(payload["query"])["bool"].get("should", [])
    assert not any("store" in c.get("match", {}) for c in should)
    clear_config_cache()


def test_store_boost_clause_lives_only_in_outer_should_not_in_function_score_filters():
    # Note: using "men fragrance" instead of "men perfume store" to avoid "store" appearing
    # in field evidence query text, while still triggering the men_perfume intent rule
    # and its contradiction terms. The test verifies the store field match clause
    # doesn't appear in function_score filters.
    payload = app.build_search_query("men fragrance", include_relevance_debug=True)

    def _collect_function_score_filters(node, acc):
        if isinstance(node, dict):
            if "function_score" in node:
                fs = node["function_score"]
                for fn in fs.get("functions", []):
                    acc.append(fn.get("filter", {}))
                _collect_function_score_filters(fs.get("query"), acc)
            for key in ("bool", "boosting"):
                if key in node and isinstance(node[key], dict):
                    for sub_key in ("query", "positive"):
                        if sub_key in node[key]:
                            _collect_function_score_filters(node[key][sub_key], acc)
        return acc

    filters = _collect_function_score_filters(payload["query"], [])
    assert filters, "test sanity: at least one function_score filter should exist for this query"
    for filt in filters:
        assert "store" not in json.dumps(filt)
