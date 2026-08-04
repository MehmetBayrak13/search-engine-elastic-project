"""
`evaluate_quality_sample.py` testleri. `requests.post` mock'lanır — gerçek
Elastic Cloud'a HİÇBİR istek atılmaz ve hiçbir belge güncellenmez."""

import json

import evaluate_quality_sample as eqs


def test_build_sample_query_uses_random_score_without_query_text():
    payload = eqs.build_sample_query(None, 25, seed=7)
    assert payload["size"] == 25
    fs = payload["query"]["function_score"]
    assert fs["query"] == {"match_all": {}}
    assert fs["random_score"]["seed"] == 7
    assert fs["boost_mode"] == "replace"


def test_build_sample_query_restricts_to_query_text():
    payload = eqs.build_sample_query("gaming mouse", 10, seed=None)
    fs = payload["query"]["function_score"]
    assert fs["query"]["multi_match"]["query"] == "gaming mouse"
    assert "seed" not in fs["random_score"]


def test_build_sample_query_uses_and_operator():
    # OR (varsayılan) operatörü çok kelimeli sorgularda ("mouse pad") yalnızca
    # TEK ortak kelimeyi (ör. "pad" -> brake pad, knee pad) içeren alakasız
    # belgeleri de örnekleme havuzuna sokuyordu (bkz. build_sample_query
    # docstring'i). AND operatörü bunu önler.
    payload = eqs.build_sample_query("mouse pad", 10, seed=None)
    assert payload["query"]["function_score"]["query"]["multi_match"]["operator"] == "and"


def test_build_report_rows_never_mutates_source_and_includes_families():
    hits = [
        {
            "_id": "B000123456",
            "_source": {
                "parent_asin": "B000123456",
                "title": "Gaming Mouse Black",
                "main_category": "Beauty & Personal Care",
                "categories": ["Beauty & Personal Care", "Makeup Brushes"],
                "categories_text": "Beauty & Personal Care Makeup Brushes",
            },
        }
    ]
    rows = eqs.build_report_rows(hits, query_group="gaming mouse")
    assert len(rows) == 1
    row = rows[0]
    assert row["query_group"] == "gaming mouse"
    assert row["parent_asin"] == "B000123456"
    assert row["main_category"] == "Beauty & Personal Care"
    assert "title_category_mismatch" in row["quality_flags"]
    assert row["title_family"] == "electronics_computers"
    assert row["category_family"] == "beauty_personal_care"
    assert row["conflicting_terms"] == ["electronics_computers", "beauty_personal_care"]
    # kaynak belge değişmemiş olmalı (offline araç yalnızca okur)
    assert "title_category_consistency" not in hits[0]["_source"]


def test_build_report_rows_defaults_query_group_to_random():
    hits = [{"_id": "B0X", "_source": {"parent_asin": "B0X", "title": "Gaming Mouse"}}]
    rows = eqs.build_report_rows(hits)
    assert rows[0]["query_group"] == eqs.RANDOM_GROUP_LABEL


def test_write_report_jsonl(tmp_path):
    rows = [{
        "query_group": "random", "parent_asin": "B0X", "title": "T",
        "main_category": "M", "source_category": "S", "categories": "C",
        "title_category_consistency": 0.1, "data_quality_score": 0.2,
        "quality_flags": ["title_category_mismatch"], "title_family": "a", "category_family": "b",
        "matched_terms": [], "conflicting_terms": ["a", "b"],
    }]
    output = tmp_path / "report.jsonl"
    eqs.write_report(rows, output)
    lines = output.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["parent_asin"] == "B0X"


def test_write_report_csv(tmp_path):
    rows = [{
        "query_group": "random", "parent_asin": "B0X", "title": "T",
        "main_category": "M", "source_category": "S", "categories": "C",
        "title_category_consistency": 0.1, "data_quality_score": 0.2,
        "quality_flags": ["title_category_mismatch", "missing_image"], "title_family": "a", "category_family": "b",
        "matched_terms": [], "conflicting_terms": ["a", "b"],
    }]
    output = tmp_path / "report.csv"
    eqs.write_report(rows, output)
    content = output.read_text(encoding="utf-8")
    assert "parent_asin" in content.splitlines()[0]
    assert "title_category_mismatch;missing_image" in content
    assert "a;b" in content


def test_fetch_sample_never_calls_non_search_endpoint(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"hits": {"hits": []}}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        return FakeResponse()

    monkeypatch.setattr(eqs.requests, "post", fake_post)
    hits = eqs.fetch_sample("https://example.es.cloud", "fake-key", "amazon-products-000001", {"size": 1})
    assert hits == []
    assert captured["url"] == "https://example.es.cloud/amazon-products-000001/_search"


def test_credentials_required(monkeypatch, capsys):
    monkeypatch.delenv("ELASTICSEARCH_URL", raising=False)
    monkeypatch.delenv("ELASTICSEARCH_API_KEY", raising=False)
    try:
        eqs._require_credentials()
        assert False, "SystemExit bekleniyordu"
    except SystemExit as exc:
        assert exc.code == 1
    captured = capsys.readouterr()
    assert "ELASTICSEARCH_URL" in captured.err


# ---------------------------------------------------------------------------
# Çoklu grup modu (rastgele + sabit sorgu grupları) — kalite kalibrasyon
# görevi için eklendi. Hiçbiri gerçek Elasticsearch'e istek atmaz.
# ---------------------------------------------------------------------------

def test_build_sample_groups_includes_random_and_queries():
    groups = eqs.build_sample_groups(5000, ["gaming mouse", "mouse pad"], 100)
    assert groups[0] == (eqs.RANDOM_GROUP_LABEL, None, 5000)
    assert groups[1] == ("gaming mouse", "gaming mouse", 100)
    assert groups[2] == ("mouse pad", "mouse pad", 100)


def test_build_sample_groups_random_size_zero_omits_random_group():
    groups = eqs.build_sample_groups(0, ["gaming mouse"], 100)
    assert groups == [("gaming mouse", "gaming mouse", 100)]


def test_build_sample_groups_empty_queries_omits_query_groups():
    groups = eqs.build_sample_groups(5000, [], 100)
    assert groups == [(eqs.RANDOM_GROUP_LABEL, None, 5000)]


def test_default_query_groups_matches_calibration_spec():
    expected = {
        "gaming mouse", "wireless mouse", "mouse pad", "makeup brush", "dog food",
        "car phone holder", "book light", "gaming chair", "pet hair vacuum",
        "bluetooth headphones", "laptop stand", "brake pad", "lipstick",
        "office chair", "coffee grinder",
    }
    assert set(eqs.DEFAULT_QUERY_GROUPS) == expected
    assert len(eqs.DEFAULT_QUERY_GROUPS) == 15


def test_fetch_all_groups_tags_rows_with_query_group_and_hits_only_search(monkeypatch):
    captured_urls = []

    class FakeResponse:
        def __init__(self, hits):
            self._hits = hits

        def raise_for_status(self):
            pass

        def json(self):
            return {"hits": {"hits": self._hits}}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured_urls.append(url)
        query_text = json["query"]["function_score"]["query"].get("multi_match", {}).get("query")
        asin = "RANDOM1" if query_text is None else f"{query_text.upper().replace(' ', '_')}1"
        return FakeResponse([{"_id": asin, "_source": {"parent_asin": asin, "title": "T"}}])

    monkeypatch.setattr(eqs.requests, "post", fake_post)
    groups = eqs.build_sample_groups(2, ["gaming mouse"], 2)
    rows = eqs.fetch_all_groups("https://example.es.cloud", "fake-key", "amazon-products-000001", groups, seed=None)

    assert all(url.endswith("/_search") for url in captured_urls)
    labels = {row["query_group"] for row in rows}
    assert labels == {eqs.RANDOM_GROUP_LABEL, "gaming mouse"}


def test_dedupe_rows_by_asin_keeps_first_occurrence():
    rows = [
        {"parent_asin": "B1", "query_group": "random"},
        {"parent_asin": "B2", "query_group": "gaming mouse"},
        {"parent_asin": "B1", "query_group": "wireless mouse"},
    ]
    unique = eqs.dedupe_rows_by_asin(rows)
    assert [row["parent_asin"] for row in unique] == ["B1", "B2"]
    assert unique[0]["query_group"] == "random"


def test_dedupe_rows_by_asin_handles_missing_asin_without_dropping():
    rows = [{"parent_asin": "", "query_group": "random"}, {"parent_asin": "", "query_group": "gaming mouse"}]
    unique = eqs.dedupe_rows_by_asin(rows)
    assert len(unique) == 2
