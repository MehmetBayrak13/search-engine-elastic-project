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
        "smartwatch",
        "men perfume", "face moisturizer", "gaming mouse",
    ]


def test_build_field_relevance_variant_configs_isolates_new_flags():
    before, after = eir.build_field_relevance_variant_configs()
    assert before.field_relevance.enabled is False
    assert before.field_consensus.enabled is False
    assert before.relevance_contradiction.enabled is False
    assert before.title_ranking.enabled == after.title_ranking.enabled
    assert after.field_relevance.enabled == eir.load_search_config().field_relevance.enabled


def test_run_variant_includes_relevance_debug_columns(monkeypatch):
    # `compute_relevance_explain` artık `matched_queries`e değil, hit'in
    # `_source` metnine bakar (bkz. services/search_service.py) — bu yüzden
    # sahte hit'in title/features alanları sorgu kelimelerini gerçekten
    # içermeli.
    def fake_search_products(query_text, config=None, include_relevance_debug=False, **kwargs):
        from services.search_models import SearchResult
        return SearchResult(
            hits=[{
                "_score": 4.2,
                "_source": {
                    "title": "Wireless Mouse X",
                    "main_category": "Computers",
                    "features": "Ergonomic wireless mouse with adjustable DPI",
                },
            }],
            total=1, error=None, current_page=1, page_size=20, total_pages=1,
            start_item=1, end_item=1, has_previous=False, has_next=False,
        )

    monkeypatch.setattr(eir.search_service, "search_products", fake_search_products)
    before, _ = eir.build_variant_configs()
    rows = eir.run_variant("wireless mouse", before, "before")
    assert rows[0]["matched_fields"] == "title|features"
    assert rows[0]["consensus_level"] == 2
    assert rows[0]["contradictions"] == ""
    assert rows[0]["applied_penalty"] == 1.0


def test_run_variant_requests_relevance_debug(monkeypatch):
    captured = {}

    def fake_search_products(query_text, config=None, include_relevance_debug=False, **kwargs):
        captured["include_relevance_debug"] = include_relevance_debug
        from services.search_models import SearchResult
        return SearchResult(
            hits=[], total=0, error=None, current_page=1, page_size=20, total_pages=0,
            start_item=0, end_item=0, has_previous=False, has_next=False,
        )

    monkeypatch.setattr(eir.search_service, "search_products", fake_search_products)
    before, _ = eir.build_variant_configs()
    eir.run_variant("wireless mouse", before, "before")
    assert captured["include_relevance_debug"] is True


def test_report_fields_include_new_columns():
    assert "matched_fields" in eir.REPORT_FIELDS
    assert "consensus_level" in eir.REPORT_FIELDS
    assert "contradictions" in eir.REPORT_FIELDS
    assert "applied_penalty" in eir.REPORT_FIELDS


def test_summarize_rank_deltas_sorts_largest_drop_first():
    rows = [
        {"query": "men perfume", "variant": "field_before", "rank": 1, "title": "Moisturizer A"},
        {"query": "men perfume", "variant": "field_after", "rank": 5, "title": "Moisturizer A"},
        {"query": "men perfume", "variant": "field_before", "rank": 2, "title": "Fragrance B"},
        {"query": "men perfume", "variant": "field_after", "rank": 1, "title": "Fragrance B"},
    ]
    deltas = eir.summarize_rank_deltas(rows, "men perfume", "field_before", "field_after")
    assert deltas[0]["title"] == "Moisturizer A"
    assert deltas[0]["rank_delta"] == 4


def test_summarize_rank_deltas_missing_after_rank_counts_as_full_drop():
    rows = [
        {"query": "men perfume", "variant": "field_before", "rank": 1, "title": "Dropped Entirely"},
    ]
    deltas = eir.summarize_rank_deltas(rows, "men perfume", "field_before", "field_after")
    assert deltas[0]["after_rank"] is None
    assert deltas[0]["rank_delta"] is None


def test_default_queries_include_new_relevance_test_queries():
    assert "men perfume" in eir.DEFAULT_QUERIES
    assert "face moisturizer" in eir.DEFAULT_QUERIES
    assert "gaming mouse" in eir.DEFAULT_QUERIES
