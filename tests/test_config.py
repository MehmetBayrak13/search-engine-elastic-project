import json
import os

import pytest

import config as config_module
from config import (
    ConfigError,
    load_intent_rules,
    load_search_config,
    load_synonyms,
    load_translations,
)


def _write_json(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _minimal_search_config(**overrides):
    base = {
        "elasticsearch": {
            "search_indices": ["idx-1", "idx-2"],
            "autocomplete_indices": ["ac-1"],
            "search_timeout_seconds": 20,
            "autocomplete_timeout_seconds": 10,
            "track_total_hits": True,
            "use_dfs_query_then_fetch": True,
        },
        "limits": {
            "result_size": 20,
            "autocomplete_fetch_size": 15,
            "autocomplete_display_size": 5,
            "autocomplete_min_chars": 3,
            "autocomplete_cache_ttl_seconds": 30,
        },
        "search_methods": {
            "exact_asin": {"field": "parent_asin", "boost": 25},
            "phrase": {"field": "title", "boost": 10},
            "fuzzy": {
                "type": "best_fields",
                "operator": "and",
                "fuzziness": "AUTO",
                "prefix_length": 2,
                "max_expansions": 30,
                "boost": 1,
                "fields": {"title": 6},
            },
            "autocomplete": {"field": "title.autocomplete", "operator": "and"},
        },
        "translation": {
            "enabled": True,
            "autocomplete_enabled": True,
            "file": "config/query_translations.json",
            "source_language": "tr",
            "target_language": "en",
            "max_variants": 3,
            "phrase_boost": 6,
            "token_boost": 2,
            "min_query_length": 2,
            "cache_ttl_seconds": 300,
        },
        "dynamic_intent": {
            "enabled": True,
            "cache_ttl_seconds": 300,
            "max_category_candidates": 5,
            "minimum_query_length": 3,
            "aggregation_size": 10,
            "boost": 8,
            "timeout_seconds": 5,
            "search_fields": {"title": 4, "categories_text": 6},
            "aggregation_fields": ["categories", "main_category"],
            "negative_category_penalty": 0.5,
        },
        "quality_ranking": {
            "enabled": False,
            "score_field": "data_quality_score",
            "consistency_field": "title_category_consistency",
            "boost": 3,
            "consistency_boost": 4,
            "low_consistency_threshold": 0.3,
            "low_consistency_penalty": 0.5,
            "bypass_for_exact_asin": True,
            "missing_value_behavior": "neutral",
            "discovery_filter_enabled": False,
            "discovery_min_data_quality_score": 0.3,
        },
        "popularity_ranking": {
            "enabled": True,
            "field": "rating_number",
            "factor": 0.03,
            "baseline": 1.0,
        },
        "unit_matching": {
            "enabled": True,
            "fields": ["title", "features"],
            "boost": 2.5,
            "min_query_length": 3,
        },
        "rating_sort": {
            "minimum_votes": 50,
            "prior_rating": 4.0,
        },
        "alternate_sort": {
            "enabled": True,
            "min_score_ratio": 0.25,
        },
        "accessory_penalty": {
            "enabled": True,
            "penalty": 0.4,
            "terms": ["case", "sticker"],
        },
        "book_title_gate": {
            "enabled": True,
            "categories": ["books"],
            "category_field": "main_category",
            "title_field": "title.keyword",
            "asin_field": "parent_asin",
            "fuzziness": "AUTO",
        },
        "pagination": {
            "enabled": True,
            "page_size": 20,
            "max_result_window": 10000,
            "max_visible_pages": 7,
        },
        "source_fields": {
            "search": ["parent_asin", "title"],
            "suggestions": ["parent_asin", "title"],
        },
        "product_url_template": "https://www.amazon.com/dp/{asin}",
        "autocomplete_ui": {
            "panel_max_height_px": 360,
            "row_height_px": 56,
            "show_images": True,
        },
        "ui": {
            "hero_logo": "🛍️",
            "hero_title": "title",
            "hero_subtitle": "subtitle",
            "search_placeholder": "placeholder",
            "placeholder_image": "https://example.com/x.png",
            "example_queries": ["a", "b"],
            "debounce_ms": 350,
            "intent_fallback_icon": "🏷️",
        },
        "title_ranking": {
            "enabled": True,
            "exact_field": "title.keyword",
            "exact_boost": 8.0,
            "prefix_boost": 3.5,
            "prefix_max_expansions": 20,
        },
        "intent_ranking": {
            "enabled": True,
            "manual_category_boost_cap": 5.0,
            "dynamic_category_boost_cap": 2.0,
            "negative_penalty_floor": 0.3,
        },
        "field_relevance": {
            "enabled": True,
            "operator": "and",
            "fields": {"title": 5.0, "features": 3.0},
            "cross_fields_boost": 3.0,
            "store_boost": 0.5,
        },
        "field_consensus": {
            "enabled": True,
            "two_field_boost": 1.15,
            "three_field_boost": 1.30,
            "four_plus_field_boost": 1.45,
            "counted_fields": ["title", "features", "description", "categories_text"],
        },
        "relevance_contradiction": {
            "enabled": True,
            "minimum_conflicting_fields": 2,
            "strong_conflicting_fields": 3,
            "mild_penalty": 0.65,
            "strong_penalty": 0.30,
            "minimum_penalty": 0.15,
            "counted_fields": ["description", "features", "categories_text"],
        },
        "relevance_debug": {"explain_top_n": 20},
    }
    for dotted_key, value in overrides.items():
        section, _, field_name = dotted_key.partition(".")
        base[section][field_name] = value
    return base


@pytest.fixture(autouse=True)
def _clear_cache():
    yield
    config_module.clear_config_cache()


def test_default_repo_config_loads_successfully():
    app_config = load_search_config()
    assert app_config.elasticsearch.search_indices
    assert app_config.limits.result_size > 0


def test_missing_config_file_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_search_config(tmp_path / "does-not-exist.json")


def test_environment_variable_overrides_json_value(tmp_path, monkeypatch):
    path = _write_json(tmp_path / "search_config.json", _minimal_search_config())
    monkeypatch.setenv("AMAZON_SEARCH_RESULT_SIZE", "99")
    app_config = load_search_config(path)
    assert app_config.limits.result_size == 99


def test_negative_timeout_is_rejected(tmp_path):
    data = _minimal_search_config()
    data["elasticsearch"]["search_timeout_seconds"] = -5
    path = _write_json(tmp_path / "search_config.json", data)
    with pytest.raises(ConfigError):
        load_search_config(path)


def test_empty_search_index_list_is_rejected(tmp_path):
    data = _minimal_search_config()
    data["elasticsearch"]["search_indices"] = []
    path = _write_json(tmp_path / "search_config.json", data)
    with pytest.raises(ConfigError):
        load_search_config(path)


def test_empty_required_field_list_is_rejected(tmp_path):
    data = _minimal_search_config()
    data["field_relevance"]["fields"] = {}
    path = _write_json(tmp_path / "search_config.json", data)
    with pytest.raises(ConfigError):
        load_search_config(path)


def test_invalid_boost_is_rejected(tmp_path):
    data = _minimal_search_config()
    data["search_methods"]["exact_asin"]["boost"] = -1
    path = _write_json(tmp_path / "search_config.json", data)
    with pytest.raises(ConfigError):
        load_search_config(path)


def test_missing_product_url_placeholder_is_rejected(tmp_path):
    data = _minimal_search_config()
    data["product_url_template"] = "https://www.amazon.com/dp/no-placeholder"
    path = _write_json(tmp_path / "search_config.json", data)
    with pytest.raises(ConfigError):
        load_search_config(path)


def test_invalid_dynamic_intent_aggregation_fields_is_rejected(tmp_path):
    data = _minimal_search_config()
    data["dynamic_intent"]["aggregation_fields"] = []
    path = _write_json(tmp_path / "search_config.json", data)
    with pytest.raises(ConfigError):
        load_search_config(path)


def test_invalid_dynamic_intent_boost_is_rejected(tmp_path):
    data = _minimal_search_config()
    data["dynamic_intent"]["boost"] = -8
    path = _write_json(tmp_path / "search_config.json", data)
    with pytest.raises(ConfigError):
        load_search_config(path)


def test_dynamic_intent_search_fields_come_from_config():
    app_config = load_search_config()
    assert app_config.dynamic_intent.es_search_fields
    assert all("^" in field for field in app_config.dynamic_intent.es_search_fields)


def test_negative_category_penalty_negative_value_is_rejected(tmp_path):
    data = _minimal_search_config()
    data["dynamic_intent"]["negative_category_penalty"] = -0.5
    path = _write_json(tmp_path / "search_config.json", data)
    with pytest.raises(ConfigError):
        load_search_config(path)


def test_negative_category_penalty_zero_is_allowed(tmp_path):
    data = _minimal_search_config()
    data["dynamic_intent"]["negative_category_penalty"] = 0
    path = _write_json(tmp_path / "search_config.json", data)
    app_config = load_search_config(path)
    assert app_config.dynamic_intent.negative_category_penalty == 0


def test_negative_category_penalty_loads_from_default_repo_config():
    app_config = load_search_config()
    assert 0 <= app_config.dynamic_intent.negative_category_penalty < 1


def test_quality_ranking_invalid_missing_value_behavior_is_rejected(tmp_path):
    data = _minimal_search_config()
    data["quality_ranking"]["missing_value_behavior"] = "bogus"
    path = _write_json(tmp_path / "search_config.json", data)
    with pytest.raises(ConfigError):
        load_search_config(path)


def test_quality_ranking_threshold_out_of_range_is_rejected(tmp_path):
    data = _minimal_search_config()
    data["quality_ranking"]["low_consistency_threshold"] = 1.5
    path = _write_json(tmp_path / "search_config.json", data)
    with pytest.raises(ConfigError):
        load_search_config(path)


def test_quality_ranking_negative_boost_is_rejected(tmp_path):
    data = _minimal_search_config()
    data["quality_ranking"]["boost"] = -3
    path = _write_json(tmp_path / "search_config.json", data)
    with pytest.raises(ConfigError):
        load_search_config(path)


def test_quality_ranking_loads_from_default_repo_config():
    app_config = load_search_config()
    assert app_config.quality_ranking.enabled is False
    assert app_config.quality_ranking.score_field == "data_quality_score"


def test_app_config_has_quality_ranking_field():
    # Regresyon: AppConfig.quality_ranking eksikse CONFIG.quality_ranking
    # erişimi "'AppConfig' object has no attribute 'quality_ranking'" ile
    # çöker (bkz. app.py: build_category_discovery_query).
    app_config = load_search_config()
    assert hasattr(app_config, "quality_ranking")
    assert app_config.quality_ranking.enabled is False
    assert app_config.quality_ranking.discovery_filter_enabled is False


def test_popularity_ranking_loads_from_default_repo_config():
    app_config = load_search_config()
    assert app_config.popularity_ranking.enabled is True
    assert app_config.popularity_ranking.field == "rating_number"
    assert app_config.popularity_ranking.factor > 0


def test_popularity_ranking_missing_section_is_rejected(tmp_path):
    data = _minimal_search_config()
    del data["popularity_ranking"]
    path = _write_json(tmp_path / "search_config.json", data)
    with pytest.raises(ConfigError):
        load_search_config(path)


def test_popularity_ranking_negative_factor_is_rejected(tmp_path):
    data = _minimal_search_config()
    data["popularity_ranking"]["factor"] = -1
    path = _write_json(tmp_path / "search_config.json", data)
    with pytest.raises(ConfigError):
        load_search_config(path)


def test_popularity_ranking_negative_baseline_is_rejected(tmp_path):
    data = _minimal_search_config()
    data["popularity_ranking"]["baseline"] = -1
    path = _write_json(tmp_path / "search_config.json", data)
    with pytest.raises(ConfigError):
        load_search_config(path)


def test_unit_matching_loads_from_default_repo_config():
    app_config = load_search_config()
    assert app_config.unit_matching.enabled is True
    assert "title" in app_config.unit_matching.fields
    assert app_config.unit_matching.boost > 0


def test_unit_matching_missing_section_is_rejected(tmp_path):
    data = _minimal_search_config()
    del data["unit_matching"]
    path = _write_json(tmp_path / "search_config.json", data)
    with pytest.raises(ConfigError):
        load_search_config(path)


def test_unit_matching_empty_fields_is_rejected(tmp_path):
    data = _minimal_search_config()
    data["unit_matching"]["fields"] = []
    path = _write_json(tmp_path / "search_config.json", data)
    with pytest.raises(ConfigError):
        load_search_config(path)


def test_unit_matching_negative_boost_is_rejected(tmp_path):
    data = _minimal_search_config()
    data["unit_matching"]["boost"] = -1
    path = _write_json(tmp_path / "search_config.json", data)
    with pytest.raises(ConfigError):
        load_search_config(path)


def test_unit_matching_can_be_disabled(tmp_path):
    data = _minimal_search_config()
    data["unit_matching"]["enabled"] = False
    path = _write_json(tmp_path / "search_config.json", data)
    app_config = load_search_config(path)
    assert app_config.unit_matching.enabled is False


def test_rating_sort_loads_from_default_repo_config():
    app_config = load_search_config()
    assert app_config.rating_sort.minimum_votes > 0
    assert 0 < app_config.rating_sort.prior_rating <= 5


def test_rating_sort_negative_minimum_votes_is_rejected(tmp_path):
    data = _minimal_search_config()
    data["rating_sort"]["minimum_votes"] = -1
    path = _write_json(tmp_path / "search_config.json", data)
    with pytest.raises(ConfigError):
        load_search_config(path)


def test_rating_sort_prior_rating_above_five_is_rejected(tmp_path):
    data = _minimal_search_config()
    data["rating_sort"]["prior_rating"] = 5.5
    path = _write_json(tmp_path / "search_config.json", data)
    with pytest.raises(ConfigError):
        load_search_config(path)


def test_rating_sort_missing_section_is_rejected(tmp_path):
    data = _minimal_search_config()
    del data["rating_sort"]
    path = _write_json(tmp_path / "search_config.json", data)
    with pytest.raises(ConfigError):
        load_search_config(path)


def test_alternate_sort_loads_from_default_repo_config():
    app_config = load_search_config()
    assert app_config.alternate_sort.enabled is True
    assert 0 <= app_config.alternate_sort.min_score_ratio <= 1


def test_alternate_sort_missing_section_is_rejected(tmp_path):
    data = _minimal_search_config()
    del data["alternate_sort"]
    path = _write_json(tmp_path / "search_config.json", data)
    with pytest.raises(ConfigError):
        load_search_config(path)


def test_alternate_sort_ratio_above_one_is_rejected(tmp_path):
    data = _minimal_search_config()
    data["alternate_sort"]["min_score_ratio"] = 1.5
    path = _write_json(tmp_path / "search_config.json", data)
    with pytest.raises(ConfigError):
        load_search_config(path)


def test_alternate_sort_ratio_zero_is_allowed(tmp_path):
    data = _minimal_search_config()
    data["alternate_sort"]["min_score_ratio"] = 0
    path = _write_json(tmp_path / "search_config.json", data)
    app_config = load_search_config(path)
    assert app_config.alternate_sort.min_score_ratio == 0


def test_pagination_loads_from_default_repo_config():
    app_config = load_search_config()
    assert app_config.pagination.enabled is True
    assert app_config.pagination.page_size == 20
    assert app_config.pagination.max_result_window == 10000
    assert app_config.pagination.max_visible_pages == 7


def test_pagination_invalid_enabled_type_is_rejected(tmp_path):
    data = _minimal_search_config()
    data["pagination"]["enabled"] = "yes"
    path = _write_json(tmp_path / "search_config.json", data)
    with pytest.raises(ConfigError):
        load_search_config(path)


def test_pagination_non_positive_page_size_is_rejected(tmp_path):
    data = _minimal_search_config()
    data["pagination"]["page_size"] = 0
    path = _write_json(tmp_path / "search_config.json", data)
    with pytest.raises(ConfigError):
        load_search_config(path)


def test_pagination_negative_max_visible_pages_is_rejected(tmp_path):
    data = _minimal_search_config()
    data["pagination"]["max_visible_pages"] = -1
    path = _write_json(tmp_path / "search_config.json", data)
    with pytest.raises(ConfigError):
        load_search_config(path)


def test_pagination_max_result_window_below_page_size_is_rejected(tmp_path):
    data = _minimal_search_config()
    data["pagination"]["page_size"] = 50
    data["pagination"]["max_result_window"] = 10
    path = _write_json(tmp_path / "search_config.json", data)
    with pytest.raises(ConfigError):
        load_search_config(path)


def test_pagination_max_result_window_equal_to_page_size_is_allowed(tmp_path):
    data = _minimal_search_config()
    data["pagination"]["page_size"] = 20
    data["pagination"]["max_result_window"] = 20
    path = _write_json(tmp_path / "search_config.json", data)
    app_config = load_search_config(path)
    assert app_config.pagination.max_result_window == 20


def test_pagination_missing_section_is_rejected(tmp_path):
    data = _minimal_search_config()
    del data["pagination"]
    path = _write_json(tmp_path / "search_config.json", data)
    with pytest.raises(ConfigError):
        load_search_config(path)


def test_pagination_max_allowed_page_property():
    data = _minimal_search_config(**{"pagination.page_size": 20})
    # max_allowed_page = max_result_window // page_size
    from config import PaginationConfig

    pagination = PaginationConfig(
        enabled=True, page_size=20, max_result_window=10000, max_visible_pages=7
    )
    assert pagination.max_allowed_page == 500


def test_app_config_has_pagination_field():
    app_config = load_search_config()
    assert hasattr(app_config, "pagination")
    assert app_config.pagination.page_size > 0


def test_autocomplete_ui_loads_from_default_repo_config():
    app_config = load_search_config()
    assert app_config.autocomplete_ui.panel_max_height_px > 0
    assert app_config.autocomplete_ui.row_height_px > 0
    assert isinstance(app_config.autocomplete_ui.show_images, bool)


def test_autocomplete_ui_missing_section_is_rejected(tmp_path):
    data = _minimal_search_config()
    del data["autocomplete_ui"]
    path = _write_json(tmp_path / "search_config.json", data)
    with pytest.raises(ConfigError):
        load_search_config(path)


def test_autocomplete_ui_negative_panel_height_is_rejected(tmp_path):
    data = _minimal_search_config(**{"autocomplete_ui.panel_max_height_px": -10})
    path = _write_json(tmp_path / "search_config.json", data)
    with pytest.raises(ConfigError):
        load_search_config(path)


def test_autocomplete_ui_invalid_show_images_type_is_rejected(tmp_path):
    data = _minimal_search_config(**{"autocomplete_ui.show_images": "yes"})
    path = _write_json(tmp_path / "search_config.json", data)
    with pytest.raises(ConfigError):
        load_search_config(path)


def test_empty_intent_rules_is_allowed(tmp_path):
    path = _write_json(tmp_path / "intent_rules.json", {})
    rules = load_intent_rules(path)
    assert rules == {}


def test_missing_intent_rules_file_is_allowed(tmp_path):
    rules = load_intent_rules(tmp_path / "does-not-exist.json")
    assert rules == {}


def test_malformed_intent_rule_is_rejected(tmp_path):
    path = _write_json(tmp_path / "intent_rules.json", {"watch": {"query_terms": "not-a-list"}})
    with pytest.raises(ConfigError):
        load_intent_rules(path)


def test_intent_rule_enabled_defaults_true(tmp_path):
    path = _write_json(
        tmp_path / "intent_rules.json",
        {"watch": {"query_terms": ["watch"], "label": "Watch", "icon": "🕒"}},
    )
    rules = load_intent_rules(path)
    assert rules["watch"].enabled is True


def test_empty_translation_dictionary_is_allowed(tmp_path):
    path = _write_json(tmp_path / "query_translations.json", {})
    translations = load_translations(path)
    assert translations.phrases == {}
    assert translations.terms == {}


def test_missing_translation_file_is_allowed(tmp_path):
    translations = load_translations(tmp_path / "does-not-exist.json")
    assert translations.phrases == {}


def test_malformed_translation_rule_is_rejected(tmp_path):
    path = _write_json(
        tmp_path / "query_translations.json",
        {"phrases": {"kablosuz kulaklık": "wireless headphones"}},
    )
    with pytest.raises(ConfigError):
        load_translations(path)


def test_empty_synonym_dictionary_is_allowed(tmp_path):
    path = _write_json(tmp_path / "synonyms.json", {})
    synonyms = load_synonyms(path)
    assert synonyms.tr_redirects == {}
    assert synonyms.en_synonyms == {}


def test_missing_synonym_file_is_allowed(tmp_path):
    synonyms = load_synonyms(tmp_path / "does-not-exist.json")
    assert synonyms.tr_redirects == {}
    assert synonyms.en_synonyms == {}


def test_synonym_tr_redirects_load_correctly(tmp_path):
    path = _write_json(tmp_path / "synonyms.json", {"tr_redirects": {"pabuç": "ayakkabı"}})
    synonyms = load_synonyms(path)
    assert synonyms.tr_redirects == {"pabuç": "ayakkabı"}


def test_synonym_en_synonyms_load_correctly(tmp_path):
    path = _write_json(tmp_path / "synonyms.json", {"en_synonyms": {"sneakers": ["trainers"]}})
    synonyms = load_synonyms(path)
    assert synonyms.en_synonyms == {"sneakers": ("trainers",)}


def test_malformed_synonym_tr_redirect_is_rejected(tmp_path):
    # tr_redirects değerleri TEK bir string olmalı (liste değil).
    path = _write_json(tmp_path / "synonyms.json", {"tr_redirects": {"pabuç": ["ayakkabı"]}})
    with pytest.raises(ConfigError):
        load_synonyms(path)


def test_malformed_synonym_en_entry_is_rejected(tmp_path):
    # en_synonyms değerleri bir LİSTE olmalı (tek string değil).
    path = _write_json(tmp_path / "synonyms.json", {"en_synonyms": {"sneakers": "trainers"}})
    with pytest.raises(ConfigError):
        load_synonyms(path)


def test_default_repo_synonyms_load_successfully():
    synonyms = load_synonyms()
    assert synonyms.tr_redirects
    assert synonyms.en_synonyms
    assert synonyms.tr_redirects.get("pabuç") == "ayakkabı"
    assert "trainers" in synonyms.en_synonyms.get("sneakers", ())


def test_title_ranking_loads_from_default_repo_config():
    app_config = load_search_config()
    assert app_config.title_ranking.enabled is True
    assert app_config.title_ranking.exact_field == "title.keyword"
    assert app_config.title_ranking.exact_boost > 0
    assert app_config.title_ranking.prefix_max_expansions > 0


def test_title_ranking_missing_section_is_rejected(tmp_path):
    data = _minimal_search_config()
    del data["title_ranking"]
    path = _write_json(tmp_path / "search_config.json", data)
    with pytest.raises(ConfigError):
        load_search_config(path)


def test_title_ranking_negative_boost_is_rejected(tmp_path):
    data = _minimal_search_config()
    data["title_ranking"]["exact_boost"] = -1
    path = _write_json(tmp_path / "search_config.json", data)
    with pytest.raises(ConfigError):
        load_search_config(path)


def test_title_ranking_non_positive_prefix_max_expansions_is_rejected(tmp_path):
    data = _minimal_search_config()
    data["title_ranking"]["prefix_max_expansions"] = 0
    path = _write_json(tmp_path / "search_config.json", data)
    with pytest.raises(ConfigError):
        load_search_config(path)


def test_intent_ranking_loads_from_default_repo_config():
    app_config = load_search_config()
    assert app_config.intent_ranking.enabled is True
    assert app_config.intent_ranking.manual_category_boost_cap > 0
    assert app_config.intent_ranking.dynamic_category_boost_cap > 0
    assert 0 < app_config.intent_ranking.negative_penalty_floor <= 1


def test_intent_ranking_missing_section_is_rejected(tmp_path):
    data = _minimal_search_config()
    del data["intent_ranking"]
    path = _write_json(tmp_path / "search_config.json", data)
    with pytest.raises(ConfigError):
        load_search_config(path)


def test_intent_ranking_negative_penalty_floor_out_of_range_is_rejected(tmp_path):
    data = _minimal_search_config()
    data["intent_ranking"]["negative_penalty_floor"] = 1.5
    path = _write_json(tmp_path / "search_config.json", data)
    with pytest.raises(ConfigError):
        load_search_config(path)


def test_intent_rule_query_terms_optional_when_all_terms_present(tmp_path):
    path = _write_json(
        tmp_path / "intent_rules.json",
        {"iphone_case": {"all_terms": ["iphone", "case"], "label": "x", "icon": ""}},
    )
    rules = load_intent_rules(path)
    assert rules["iphone_case"].query_terms == ()
    assert rules["iphone_case"].all_terms == ("iphone", "case")


def test_intent_rule_without_any_trigger_terms_is_rejected(tmp_path):
    path = _write_json(
        tmp_path / "intent_rules.json",
        {"broken": {"label": "x", "icon": ""}},
    )
    with pytest.raises(ConfigError):
        load_intent_rules(path)


def test_intent_rule_positive_categories_parsed(tmp_path):
    path = _write_json(
        tmp_path / "intent_rules.json",
        {
            "iphone_case": {
                "all_terms": ["iphone", "case"],
                "positive_categories": [{"value": "Cases", "boost": 3.0}],
                "label": "x",
                "icon": "",
            }
        },
    )
    rules = load_intent_rules(path)
    assert rules["iphone_case"].positive_categories[0].value == "Cases"
    assert rules["iphone_case"].positive_categories[0].boost == 3.0


def test_intent_rule_negative_categories_legacy_string_shape_unchanged(tmp_path):
    path = _write_json(
        tmp_path / "intent_rules.json",
        {"watch": {"query_terms": ["watch"], "negative_categories": ["books"], "label": "x", "icon": ""}},
    )
    rules = load_intent_rules(path)
    assert rules["watch"].negative_categories == ("books",)
    assert rules["watch"].soft_negative_categories == ()


def test_intent_rule_negative_categories_new_object_shape(tmp_path):
    path = _write_json(
        tmp_path / "intent_rules.json",
        {
            "iphone_case": {
                "all_terms": ["iphone", "case"],
                "negative_categories": [{"value": "Cell Phones", "penalty": 0.5}],
                "label": "x",
                "icon": "",
            }
        },
    )
    rules = load_intent_rules(path)
    assert rules["iphone_case"].negative_categories == ()
    assert rules["iphone_case"].soft_negative_categories[0].value == "Cell Phones"
    assert rules["iphone_case"].soft_negative_categories[0].penalty == 0.5


def test_intent_rule_negative_categories_mixed_shape_is_rejected(tmp_path):
    path = _write_json(
        tmp_path / "intent_rules.json",
        {
            "broken": {
                "query_terms": ["x"],
                "negative_categories": ["books", {"value": "Cell Phones", "penalty": 0.5}],
                "label": "x",
                "icon": "",
            }
        },
    )
    with pytest.raises(ConfigError):
        load_intent_rules(path)


def test_intent_rule_negative_category_penalty_out_of_range_is_rejected(tmp_path):
    path = _write_json(
        tmp_path / "intent_rules.json",
        {
            "broken": {
                "query_terms": ["x"],
                "negative_categories": [{"value": "Cell Phones", "penalty": 1.5}],
                "label": "x",
                "icon": "",
            }
        },
    )
    with pytest.raises(ConfigError):
        load_intent_rules(path)


def test_iphone_case_rule_loads_from_default_repo_config():
    rules = load_intent_rules()
    assert "iphone_case" in rules
    rule = rules["iphone_case"]
    assert set(rule.all_terms) == {"iphone", "case"}
    assert rule.positive_categories
    assert rule.soft_negative_categories


def test_field_relevance_loads_from_default_repo_config():
    app_config = load_search_config()
    assert app_config.field_relevance.enabled is True
    assert app_config.field_relevance.fields
    assert app_config.field_relevance.cross_fields_boost > 0
    assert app_config.field_relevance.store_boost >= 0


def test_field_relevance_canonical_fields_dedupes_tr_variant():
    app_config = load_search_config()
    canonical = app_config.field_relevance.canonical_fields
    assert "title" in canonical
    assert "title.tr" not in canonical
    assert len(canonical) == len(set(canonical))


def test_field_relevance_missing_section_is_rejected(tmp_path):
    data = _minimal_search_config()
    del data["field_relevance"]
    path = _write_json(tmp_path / "search_config.json", data)
    with pytest.raises(ConfigError):
        load_search_config(path)


def test_field_relevance_negative_cross_fields_boost_is_rejected(tmp_path):
    data = _minimal_search_config()
    data["field_relevance"]["cross_fields_boost"] = -1
    path = _write_json(tmp_path / "search_config.json", data)
    with pytest.raises(ConfigError):
        load_search_config(path)


def test_search_methods_no_longer_has_multi_match():
    app_config = load_search_config()
    assert not hasattr(app_config.search_methods, "multi_match")


def test_field_consensus_loads_from_default_repo_config():
    app_config = load_search_config()
    assert app_config.field_consensus.enabled is True
    assert app_config.field_consensus.two_field_boost > 1.0
    assert app_config.field_consensus.counted_fields


def test_field_consensus_missing_section_is_rejected(tmp_path):
    data = _minimal_search_config()
    del data["field_consensus"]
    path = _write_json(tmp_path / "search_config.json", data)
    with pytest.raises(ConfigError):
        load_search_config(path)


def test_field_consensus_tiers_must_be_non_decreasing(tmp_path):
    data = _minimal_search_config()
    data["field_consensus"]["three_field_boost"] = 1.0  # below two_field_boost (1.15)
    path = _write_json(tmp_path / "search_config.json", data)
    with pytest.raises(ConfigError):
        load_search_config(path)


def test_relevance_contradiction_loads_from_default_repo_config():
    app_config = load_search_config()
    assert app_config.relevance_contradiction.enabled is True
    assert 0 < app_config.relevance_contradiction.minimum_penalty <= app_config.relevance_contradiction.strong_penalty
    assert app_config.relevance_contradiction.strong_penalty < app_config.relevance_contradiction.mild_penalty


def test_relevance_contradiction_missing_section_is_rejected(tmp_path):
    data = _minimal_search_config()
    del data["relevance_contradiction"]
    path = _write_json(tmp_path / "search_config.json", data)
    with pytest.raises(ConfigError):
        load_search_config(path)


def test_relevance_contradiction_penalty_ordering_is_enforced(tmp_path):
    data = _minimal_search_config()
    data["relevance_contradiction"]["strong_penalty"] = 0.9  # not < mild_penalty (0.65)
    path = _write_json(tmp_path / "search_config.json", data)
    with pytest.raises(ConfigError):
        load_search_config(path)


def test_relevance_contradiction_penalty_below_floor_is_rejected(tmp_path):
    data = _minimal_search_config()
    data["relevance_contradiction"]["minimum_penalty"] = 0.5  # above strong_penalty (0.30)
    path = _write_json(tmp_path / "search_config.json", data)
    with pytest.raises(ConfigError):
        load_search_config(path)


def test_relevance_contradiction_strong_fields_must_exceed_minimum(tmp_path):
    data = _minimal_search_config()
    data["relevance_contradiction"]["strong_conflicting_fields"] = 2  # not > minimum (2)
    path = _write_json(tmp_path / "search_config.json", data)
    with pytest.raises(ConfigError):
        load_search_config(path)


def test_relevance_debug_loads_from_default_repo_config():
    app_config = load_search_config()
    assert app_config.relevance_debug.explain_top_n > 0


def test_relevance_debug_non_positive_top_n_is_rejected(tmp_path):
    data = _minimal_search_config()
    data["relevance_debug"] = {"explain_top_n": 0}
    path = _write_json(tmp_path / "search_config.json", data)
    with pytest.raises(ConfigError):
        load_search_config(path)


def test_intent_rule_contradiction_terms_parsed(tmp_path):
    path = _write_json(
        tmp_path / "intent_rules.json",
        {
            "men_perfume": {
                "all_terms": ["men", "perfume"],
                "contradiction_terms": ["moisturizer", "face cream"],
                "label": "x",
                "icon": "",
            }
        },
    )
    rules = load_intent_rules(path)
    assert rules["men_perfume"].contradiction_terms == ("moisturizer", "face cream")


def test_intent_rule_contradiction_terms_default_empty(tmp_path):
    path = _write_json(
        tmp_path / "intent_rules.json",
        {"watch": {"query_terms": ["watch"], "label": "x", "icon": ""}},
    )
    rules = load_intent_rules(path)
    assert rules["watch"].contradiction_terms == ()


def test_men_perfume_rule_loads_from_default_repo_config():
    rules = load_intent_rules()
    assert "men_perfume" in rules
    rule = rules["men_perfume"]
    assert set(rule.all_terms) == {"men", "perfume"}
    assert rule.contradiction_terms
    assert rule.positive_categories
