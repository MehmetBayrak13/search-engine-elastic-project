from dataclasses import replace

from services import autocomplete_service, search_service

from services.search_service import _build_did_you_mean, _build_spell_suggest_block


def test_build_spell_suggest_block_shape():
    block = _build_spell_suggest_block("wireless mouse", search_service.CONFIG)
    assert block == {
        "spell_suggestion": {
            "text": "wireless mouse",
            "term": {
                "field": search_service.CONFIG.spell_suggest.field,
                "size": search_service.CONFIG.spell_suggest.suggestion_size,
                "suggest_mode": "missing",
            },
        }
    }


def test_build_spell_suggest_block_none_when_disabled():
    off_cfg = replace(search_service.CONFIG, spell_suggest=replace(search_service.CONFIG.spell_suggest, enabled=False))
    assert _build_spell_suggest_block("wireless mouse", off_cfg) is None


def test_build_spell_suggest_block_none_for_empty_query():
    assert _build_spell_suggest_block("", search_service.CONFIG) is None
    assert _build_spell_suggest_block("   ", search_service.CONFIG) is None


def test_search_query_includes_suggest_block_when_enabled():
    payload = search_service.build_search_query("wireless mouse", apply_intent_reranking=False)
    assert "suggest" in payload
    assert payload["suggest"]["spell_suggestion"]["text"] == "wireless mouse"


def test_search_query_omits_suggest_block_when_disabled(tmp_path_factory):
    import json
    from pathlib import Path
    from config import load_search_config
    from services.search_service import build_search_query

    data = json.loads(Path("config/search_config.json").read_text(encoding="utf-8"))
    data["spell_suggest"]["enabled"] = False
    path = tmp_path_factory.mktemp("cfg") / "search_config.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    override_config = load_search_config(path)

    payload = build_search_query("wireless mouse", config=override_config, apply_intent_reranking=False)
    assert "suggest" not in payload


def test_build_did_you_mean_reconstructs_multi_word_correction():
    suggest_response = {
        "spell_suggestion": [
            {"text": "blootooth", "offset": 0, "length": 9,
             "options": [{"text": "bluetooth", "score": 0.78, "freq": 2291}]},
            {"text": "heaphones", "offset": 10, "length": 9,
             "options": [{"text": "headphones", "score": 0.89, "freq": 482}]},
        ]
    }
    assert _build_did_you_mean("blootooth heaphones", suggest_response, search_service.CONFIG) == "bluetooth headphones"


def test_build_did_you_mean_ignores_low_confidence_options():
    suggest_response = {
        "spell_suggestion": [
            {"text": "maus", "offset": 0, "length": 4,
             "options": [{"text": "maui", "score": 0.2, "freq": 3}]},
        ]
    }
    assert _build_did_you_mean("maus", suggest_response, search_service.CONFIG) is None


def test_build_did_you_mean_returns_none_when_no_entries():
    assert _build_did_you_mean("wireless mouse", {}, search_service.CONFIG) is None
    assert _build_did_you_mean("wireless mouse", {"spell_suggestion": []}, search_service.CONFIG) is None


def test_build_did_you_mean_returns_none_when_nothing_actually_changes():
    # options bulunsa bile en iyi seçenek zaten yazılan kelimenin aynısıysa
    # gerçek bir "düzeltme" değildir.
    suggest_response = {
        "spell_suggestion": [
            {"text": "mouse", "offset": 0, "length": 5,
             "options": [{"text": "mouse", "score": 0.9, "freq": 100}]},
        ]
    }
    assert _build_did_you_mean("mouse", suggest_response, search_service.CONFIG) is None


def test_search_products_surfaces_did_you_mean_only_when_results_are_sparse(monkeypatch):
    from services import search_service

    def fake_post_search(payload, timeout=20, index=None, search_type=None):
        if payload.get("size") == 0:
            return {"aggregations": {}}, None
        return {
            "hits": {"hits": [], "total": {"value": 0}},
            "suggest": {
                "spell_suggestion": [
                    {"text": "heaphones", "offset": 0, "length": 9,
                     "options": [{"text": "headphones", "score": 0.9, "freq": 500}]},
                ]
            },
        }, None

    monkeypatch.setattr(search_service, "_post_search", fake_post_search)
    result = search_service.search_products("heaphones")
    assert result.total == 0
    assert result.did_you_mean == "headphones"


def test_search_products_suppresses_did_you_mean_when_results_are_plentiful(monkeypatch):
    from services import search_service

    def fake_post_search(payload, timeout=20, index=None, search_type=None):
        if payload.get("size") == 0:
            return {"aggregations": {}}, None
        return {
            "hits": {"hits": [{"_id": "x", "_score": 1.0, "_source": {}}] * 10, "total": {"value": 500}},
            "suggest": {
                "spell_suggestion": [
                    {"text": "heaphones", "offset": 0, "length": 9,
                     "options": [{"text": "headphones", "score": 0.9, "freq": 500}]},
                ]
            },
        }, None

    monkeypatch.setattr(search_service, "_post_search", fake_post_search)
    result = search_service.search_products("heaphones")
    assert result.total == 500
    assert result.did_you_mean is None
