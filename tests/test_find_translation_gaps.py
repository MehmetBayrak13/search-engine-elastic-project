import tools.find_translation_gaps as ftg
from config import SynonymDictionary, TranslationDictionary


def test_build_covered_vocabulary_flattens_translation_and_synonym_targets(monkeypatch):
    monkeypatch.setattr(
        ftg.search_service, "TRANSLATIONS",
        TranslationDictionary(phrases={"kablosuz kulaklık": ("wireless headphones",)}, terms={"araba": ("car",)}),
    )
    monkeypatch.setattr(
        ftg.search_service, "SYNONYMS",
        SynonymDictionary(tr_redirects={}, en_synonyms={"sneakers": ("trainers",)}),
    )
    vocab = ftg.build_covered_vocabulary()
    assert {"wireless", "headphones", "car", "trainers"} <= vocab


def test_compute_coverage_flags_fully_uncovered_category():
    coverage, uncovered = ftg.compute_coverage("gaming mouse pads", set())
    assert coverage == 0.0
    assert set(uncovered) == {"gaming", "mouse", "pads"}


def test_compute_coverage_ignores_stopwords_and_short_words():
    # "of", "and" stopword; "to" 2 karakter -- kapsam hesabına hiç girmemeli.
    coverage, uncovered = ftg.compute_coverage("tools and accessories", {"tools"})
    assert coverage == 1.0
    assert uncovered == []


def test_compute_coverage_partial_match():
    coverage, uncovered = ftg.compute_coverage("gaming mouse", {"mouse"})
    assert coverage == 0.5
    assert uncovered == ["gaming"]


def test_find_gaps_only_reports_categories_below_threshold(monkeypatch):
    monkeypatch.setattr(
        ftg.search_service, "TRANSLATIONS",
        TranslationDictionary(phrases={}, terms={"kahve": ("coffee",), "öğütücü": ("grinder",)}),
    )
    monkeypatch.setattr(ftg.search_service, "SYNONYMS", SynonymDictionary(tr_redirects={}, en_synonyms={}))

    def fake_post_search(payload):
        assert payload["size"] == 0
        assert "aggs" in payload
        return {
            "aggregations": {
                "cats": {
                    "buckets": [
                        {"key": "coffee grinder", "doc_count": 500},   # fully covered
                        {"key": "cat food", "doc_count": 300},         # fully uncovered
                    ]
                }
            }
        }, None

    monkeypatch.setattr(ftg.search_service, "_post_search", fake_post_search)
    gaps = ftg.find_gaps(size=10, min_rating_number=10, max_coverage=0.5)
    assert len(gaps) == 1
    assert gaps[0]["category"] == "cat food"
    assert gaps[0]["doc_count"] == 300


def test_find_gaps_raises_on_elasticsearch_error(monkeypatch):
    monkeypatch.setattr(ftg.search_service, "_post_search", lambda payload: (None, "boom"))
    try:
        ftg.find_gaps(size=10, min_rating_number=10, max_coverage=0.5)
        assert False, "SystemExit bekleniyordu"
    except SystemExit:
        pass


def test_write_report_json_and_csv(tmp_path):
    rows = [{"category": "cat food", "doc_count": 300, "coverage": 0.0, "uncovered_words": "cat, food"}]
    json_path = tmp_path / "gaps.json"
    ftg.write_report(rows, json_path, fmt="json")
    assert "cat food" in json_path.read_text(encoding="utf-8")

    csv_path = tmp_path / "gaps.csv"
    ftg.write_report(rows, csv_path, fmt="csv")
    content = csv_path.read_text(encoding="utf-8")
    assert "category" in content.splitlines()[0]
    assert "cat food" in content
