"""
Importer'ların (`full_amazon_importer.py`, `index_amazon.py`) ürün kalite
entegrasyonu testleri. Hugging Face / Elasticsearch'e HİÇBİR istek atılmaz —
yalnızca saf dönüşüm/entegrasyon fonksiyonları test edilir.
"""

import json

import full_amazon_importer as importer
import index_amazon as legacy_importer


SAMPLE_PRODUCT = {
    "parent_asin": "B000123456",
    "title": "Gaming Mouse Black",
}


def _base_document():
    return dict(SAMPLE_PRODUCT, categories=["Beauty & Personal Care", "Makeup Brushes"])


# ---------------------------------------------------------------------------
# full_amazon_importer.py
# ---------------------------------------------------------------------------

def test_apply_quality_evaluation_adds_quality_fields():
    document = _base_document()
    result = importer.apply_quality_evaluation(
        document, category="Beauty_and_Personal_Care", file_path="raw_meta_x/part.parquet", row_number=0
    )
    assert result is document  # yerinde günceller
    assert "title_category_consistency" in result
    assert "data_quality_score" in result
    assert "quality_flags" in result
    assert result["quality_version"] == "v1"
    assert "title_category_mismatch" in result["quality_flags"]


def test_apply_quality_evaluation_failure_does_not_raise_and_logs(tmp_path, monkeypatch):
    error_log = tmp_path / "import_errors.jsonl"
    monkeypatch.setattr(importer, "ERROR_LOG_FILE", error_log)
    monkeypatch.setattr(importer, "PROGRESS_LOG_FILE", tmp_path / "progress.log")

    def boom(*args, **kwargs):
        raise RuntimeError("forced quality evaluator failure")

    monkeypatch.setattr(importer, "evaluate_product_quality", boom)

    document = _base_document()
    result = importer.apply_quality_evaluation(
        document, category="Beauty_and_Personal_Care", file_path="raw_meta_x/part.parquet", row_number=7
    )

    assert result["quality_flags"] == ["quality_evaluation_failed"]
    assert result["title_category_consistency"] == 0.5
    assert result["data_quality_score"] == 0.5

    assert error_log.exists()
    logged = json.loads(error_log.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert logged["category"] == "Beauty_and_Personal_Care"
    assert logged["row_number"] == 7
    assert "forced quality evaluator failure" in logged["error"]


def test_transform_product_is_unaffected_by_quality_integration():
    # transform_product yalnızca alan normalizasyonu yapar; kalite alanları
    # onun İÇİNDE değil, iterate_file_documents akışında sonradan eklenir.
    document = importer.transform_product(
        {"parent_asin": "B0X", "title": "Gaming Mouse", "categories": ["Electronics"]},
        "Electronics",
    )
    assert "title_category_consistency" not in document
    assert "data_quality_score" not in document


def test_checkpoint_round_trip_unaffected(tmp_path, monkeypatch):
    checkpoint_file = tmp_path / "checkpoint.json"
    monkeypatch.setattr(importer, "CHECKPOINT_FILE", checkpoint_file)

    checkpoint = importer.default_checkpoint()
    checkpoint["completed_files"] = ["raw_meta_x/part-0.parquet"]
    checkpoint["total_success"] = 42
    importer.save_checkpoint(checkpoint)

    reloaded = importer.load_checkpoint()
    assert reloaded["completed_files"] == ["raw_meta_x/part-0.parquet"]
    assert reloaded["total_success"] == 42


# ---------------------------------------------------------------------------
# index_amazon.py
# ---------------------------------------------------------------------------

def test_legacy_importer_apply_quality_evaluation_adds_fields():
    document = _base_document()
    result = legacy_importer.apply_quality_evaluation(document, "Beauty_and_Personal_Care")
    assert result is document
    assert "data_quality_score" in result
    assert result["quality_version"] == "v1"


def test_legacy_importer_quality_failure_falls_back_without_raising(monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("forced failure")

    monkeypatch.setattr(legacy_importer, "evaluate_product_quality", boom)

    document = _base_document()
    result = legacy_importer.apply_quality_evaluation(document, "Beauty_and_Personal_Care")

    assert result["quality_flags"] == ["quality_evaluation_failed"]
    captured = capsys.readouterr()
    assert "UYARI" in captured.out


def test_legacy_importer_shares_product_quality_module_not_a_copy():
    import product_quality

    assert legacy_importer.evaluate_product_quality is product_quality.evaluate_product_quality
    assert importer.evaluate_product_quality is product_quality.evaluate_product_quality
