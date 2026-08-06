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
    payload = app.build_search_query(
        "kamera",
        enable_phrase=False,
        enable_multi_match=False,
        enable_fuzzy=False,
        enable_exact_asin=False,
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
    asin_clauses = [c for c in lexical if "term" in c]
    assert len(asin_clauses) == 1
    field = app.CONFIG.search_methods.exact_asin.field
    boost = app.CONFIG.search_methods.exact_asin.boost
    assert asin_clauses[0]["term"][field]["boost"] == boost


def test_fuzzy_switch_can_be_disabled():
    with_fuzzy = app.build_search_query("kamera", apply_intent_reranking=False)
    without_fuzzy = app.build_search_query(
        "kamera", enable_fuzzy=False, apply_intent_reranking=False
    )
    with_count = len(with_fuzzy["query"]["bool"]["must"][0]["bool"]["should"])
    without_count = len(without_fuzzy["query"]["bool"]["must"][0]["bool"]["should"])
    assert without_count == with_count - 1


def test_multi_match_uses_configured_fields_and_boost():
    payload = app.build_search_query(
        "kamera",
        enable_phrase=False,
        enable_fuzzy=False,
        enable_exact_asin=False,
        apply_intent_reranking=False,
    )
    clause = payload["query"]["bool"]["must"][0]["bool"]["should"][0]["multi_match"]
    assert clause["fields"] == app.CONFIG.search_methods.multi_match.es_fields
    assert clause["boost"] == app.CONFIG.search_methods.multi_match.boost
    assert clause["operator"] == app.CONFIG.search_methods.multi_match.operator


def test_autocomplete_uses_configured_field_and_operator():
    payload = app.build_autocomplete_query("kam", apply_intent_reranking=False)
    inner = payload["query"]["bool"]["must"][0]["bool"]["should"][0]["match"]
    field = app.CONFIG.search_methods.autocomplete.field
    assert field in inner
    assert inner[field]["operator"] == app.CONFIG.search_methods.autocomplete.operator


def test_autocomplete_result_size_defaults_from_config():
    payload = app.build_autocomplete_query("kam")
    assert payload["size"] == app.CONFIG.limits.autocomplete_fetch_size


def test_detect_search_intent_watch():
    info = app.detect_search_intent("erkek kol saati")
    assert info["intent"] == "watch"
    assert info["apply_exclusion"] is True


def test_detect_search_intent_none_for_unrelated_query():
    info = app.detect_search_intent("wireless headphones")
    assert info["intent"] is None
