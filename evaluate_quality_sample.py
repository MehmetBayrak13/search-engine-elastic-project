"""
Offline kalite değerlendirme raporu — Elasticsearch'ten READ-ONLY örnek ürün
çeker, `product_quality.evaluate_product_quality`yi çalıştırır ve bir
CSV/JSONL raporu üretir. HİÇBİR belgeyi güncellemez/indexlemez/silmez;
yalnızca `_search` (GET amaçlı, mutasyon içermeyen) isteği atar.

Kullanım:
    # Ortam değişkenleri (app.py ile aynı şema):
    export ELASTICSEARCH_URL="https://<deployment>.es.<region>.cloud.es.io"
    export ELASTICSEARCH_API_KEY="<api_key>"

    python evaluate_quality_sample.py --sample-size 200 --output report.jsonl
    python evaluate_quality_sample.py --query "gaming mouse" --sample-size 50 --output report.csv
    python evaluate_quality_sample.py --seed 42 --index amazon-products-000001

Rapor alanları: parent_asin, title, categories, title_category_consistency,
data_quality_score, quality_flags, predicted_family (title'dan çıkarılan
kategori ailesi), category_family (categories'ten çıkarılan aile).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path
from typing import Any

import requests

from product_quality import evaluate_product_quality

DEFAULT_SEARCH_INDICES = "amazon-products-000001,amazon-products-000002"
DEFAULT_OUTPUT = "quality_sample_report.jsonl"
REQUEST_TIMEOUT_SECONDS = 30

REPORT_FIELDS = [
    "parent_asin",
    "title",
    "categories",
    "title_category_consistency",
    "data_quality_score",
    "quality_flags",
    "predicted_family",
    "category_family",
]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Elasticsearch'ten read-only örnek ürün çeker, ürün kalite "
            "algoritmasını çalıştırır ve CSV/JSONL raporu üretir. Hiçbir "
            "belgeyi güncellemez."
        )
    )
    parser.add_argument("--sample-size", type=int, default=50, help="Örneklenecek belge sayısı (varsayılan: 50).")
    parser.add_argument(
        "--query",
        type=str,
        default=None,
        help="Verilirse örneklem bu metinle eşleşen belgelerle sınırlandırılır (title/categories_text).",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=DEFAULT_OUTPUT,
        help=f"Rapor dosyası (.csv veya .jsonl uzantısına göre biçim seçilir; varsayılan: {DEFAULT_OUTPUT}).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Rastgele örnekleme için sabit seed (verilmezse her çalıştırmada farklı örneklem).",
    )
    parser.add_argument(
        "--index",
        type=str,
        default=DEFAULT_SEARCH_INDICES,
        help=f"Virgülle ayrılmış index listesi (varsayılan: {DEFAULT_SEARCH_INDICES}).",
    )
    return parser.parse_args(argv)


def _require_credentials() -> tuple[str, str]:
    url = os.getenv("ELASTICSEARCH_URL")
    api_key = os.getenv("ELASTICSEARCH_API_KEY")
    if not url or not api_key:
        missing = [name for name, value in [("ELASTICSEARCH_URL", url), ("ELASTICSEARCH_API_KEY", api_key)] if not value]
        print(f"HATA: eksik ortam değişken(ler)i: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)
    return url.rstrip("/"), api_key


def build_sample_query(query_text: str | None, sample_size: int, seed: int | None) -> dict[str, Any]:
    """Read-only örnekleme sorgusu. `query_text` verilirse eşleşen belgeler
    arasından, verilmezse tüm index'ten rastgele örneklenir (`random_score`).
    Hiçbir yazma/güncelleme operatörü içermez."""
    base_query: dict[str, Any] = {"match_all": {}}
    if query_text:
        base_query = {
            "multi_match": {
                "query": query_text,
                "fields": ["title^3", "categories_text", "categories_text.tr"],
            }
        }

    random_score: dict[str, Any] = {"field": "_seq_no"}
    if seed is not None:
        random_score["seed"] = seed

    return {
        "size": sample_size,
        "track_total_hits": False,
        "_source": True,
        "query": {
            "function_score": {
                "query": base_query,
                "random_score": random_score,
                "boost_mode": "replace",
            }
        },
    }


def fetch_sample(url: str, api_key: str, index: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    headers = {"Authorization": f"ApiKey {api_key}", "Content-Type": "application/json"}
    response = requests.post(f"{url}/{index}/_search", headers=headers, json=payload, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    data = response.json()
    return data.get("hits", {}).get("hits", [])


def _categories_display(source: dict[str, Any]) -> str:
    categories = source.get("categories")
    if isinstance(categories, list):
        return " | ".join(str(c) for c in categories)
    if isinstance(categories, str):
        return categories
    return ""


def build_report_rows(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for hit in hits:
        source = hit.get("_source", {}) or {}
        quality = evaluate_product_quality(source, include_explanation=True)
        signals = quality.get("quality_explanation", {}).get("signals", {})
        rows.append({
            "parent_asin": source.get("parent_asin") or hit.get("_id") or "",
            "title": source.get("title") or "",
            "categories": _categories_display(source),
            "title_category_consistency": quality["title_category_consistency"],
            "data_quality_score": quality["data_quality_score"],
            "quality_flags": quality["quality_flags"],
            "predicted_family": signals.get("title_family"),
            "category_family": signals.get("category_family"),
        })
    return rows


def write_report(rows: list[dict[str, Any]], output_path: Path) -> None:
    if output_path.suffix.lower() == ".csv":
        with output_path.open("w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=REPORT_FIELDS)
            writer.writeheader()
            for row in rows:
                csv_row = dict(row)
                csv_row["quality_flags"] = ";".join(row["quality_flags"])
                writer.writerow(csv_row)
    else:
        with output_path.open("w", encoding="utf-8") as file:
            for row in rows:
                file.write(json.dumps(row, ensure_ascii=False) + "\n")


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    url, api_key = _require_credentials()

    payload = build_sample_query(args.query, args.sample_size, args.seed)
    hits = fetch_sample(url, api_key, args.index, payload)

    if not hits:
        print("Örneklemde hiç belge bulunamadı.", file=sys.stderr)
        sys.exit(0)

    rows = build_report_rows(hits)
    output_path = Path(args.output)
    write_report(rows, output_path)

    mismatch_count = sum(1 for row in rows if "title_category_mismatch" in row["quality_flags"])
    print(f"{len(rows)} belge değerlendirildi, rapor yazıldı: {output_path}")
    print(f"title_category_mismatch flag'i alan belge sayısı: {mismatch_count}")


if __name__ == "__main__":
    main()
