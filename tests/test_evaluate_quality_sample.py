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


def test_build_report_rows_never_mutates_source_and_includes_families():
    hits = [
        {
            "_id": "B000123456",
            "_source": {
                "parent_asin": "B000123456",
                "title": "Gaming Mouse Black",
                "categories": ["Beauty & Personal Care", "Makeup Brushes"],
                "categories_text": "Beauty & Personal Care Makeup Brushes",
            },
        }
    ]
    rows = eqs.build_report_rows(hits)
    assert len(rows) == 1
    row = rows[0]
    assert row["parent_asin"] == "B000123456"
    assert "title_category_mismatch" in row["quality_flags"]
    assert row["predicted_family"] == "electronics_computers"
    assert row["category_family"] == "beauty_personal_care"
    # kaynak belge değişmemiş olmalı (offline araç yalnızca okur)
    assert "title_category_consistency" not in hits[0]["_source"]


def test_write_report_jsonl(tmp_path):
    rows = [{
        "parent_asin": "B0X", "title": "T", "categories": "C",
        "title_category_consistency": 0.1, "data_quality_score": 0.2,
        "quality_flags": ["title_category_mismatch"], "predicted_family": "a", "category_family": "b",
    }]
    output = tmp_path / "report.jsonl"
    eqs.write_report(rows, output)
    lines = output.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["parent_asin"] == "B0X"


def test_write_report_csv(tmp_path):
    rows = [{
        "parent_asin": "B0X", "title": "T", "categories": "C",
        "title_category_consistency": 0.1, "data_quality_score": 0.2,
        "quality_flags": ["title_category_mismatch", "missing_image"], "predicted_family": "a", "category_family": "b",
    }]
    output = tmp_path / "report.csv"
    eqs.write_report(rows, output)
    content = output.read_text(encoding="utf-8")
    assert "parent_asin" in content.splitlines()[0]
    assert "title_category_mismatch;missing_image" in content


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
