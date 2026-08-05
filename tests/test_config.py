import json
import os

import pytest

import config as config_module
from config import (
    ConfigError,
    load_intent_rules,
    load_search_config,
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
            "multi_match": {
                "type": "best_fields",
                "operator": "and",
                "boost": 4,
                "fields": {"title": 7},
            },
            "fuzzy": {
                "type": "best_fields",
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
    data["search_methods"]["multi_match"]["fields"] = {}
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
