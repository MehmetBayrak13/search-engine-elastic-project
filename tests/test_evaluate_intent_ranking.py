import json

import tools.evaluate_intent_ranking as eir


def test_build_variant_configs_before_has_new_features_disabled():
    before, after = eir.build_variant_configs()
    assert before.title_ranking.enabled is False
    assert before.intent_ranking.enabled is False
    assert after.title_ranking.enabled == eir.load_search_config().title_ranking.enabled


def test_run_variant_only_calls_search_products_never_writes(monkeypatch):
    calls = []

    def fake_search_products(query_text, config=None, **kwargs):
        calls.append((query_text, config))
        from services.search_models import SearchResult
        return SearchResult(
            hits=[{"_score": 4.2, "_source": {"title": "Wireless Mouse X", "main_category": "Computers"}}],
            total=1, error=None, current_page=1, page_size=20, total_pages=1,
            start_item=1, end_item=1, has_previous=False, has_next=False,
        )

    monkeypatch.setattr(eir.search_service, "search_products", fake_search_products)
    before, _ = eir.build_variant_configs()
    rows = eir.run_variant("wireless mouse", before, "before")
    assert len(calls) == 1
    assert rows[0]["title"] == "Wireless Mouse X"
    assert rows[0]["variant"] == "before"
    assert rows[0]["rank"] == 1


def test_write_report_json(tmp_path):
    rows = [{"query": "x", "variant": "before", "rank": 1, "title": "t", "category": "c", "score": 1.0, "matched_rule": None, "discovered_category": None}]
    path = tmp_path / "report.json"
    eir.write_report(rows, path, fmt="json")
    assert json.loads(path.read_text(encoding="utf-8")) == rows


def test_write_report_csv(tmp_path):
    rows = [{"query": "x", "variant": "before", "rank": 1, "title": "t", "category": "c", "score": 1.0, "matched_rule": None, "discovered_category": None}]
    path = tmp_path / "report.csv"
    eir.write_report(rows, path, fmt="csv")
    content = path.read_text(encoding="utf-8")
    assert "query" in content.splitlines()[0]
    assert "x" in content


def test_default_queries_match_evaluation_spec():
    assert eir.DEFAULT_QUERIES == [
        "wireless mouse", "iphone case", "running shoes", "gaming keyboard",
        "coffee maker", "dog food", "laptop stand", "usb c cable",
        "phone charger", "bluetooth headphones",
    ]
