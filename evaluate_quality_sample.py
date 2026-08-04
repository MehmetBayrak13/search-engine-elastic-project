"""
Offline kalite değerlendirme raporu — Elasticsearch'ten READ-ONLY örnek ürün
çeker, `product_quality.evaluate_product_quality`yi çalıştırır ve bir
CSV/JSONL raporu üretir. HİÇBİR belgeyi güncellemez/indexlemez/silmez;
yalnızca `_search` (GET amaçlı, mutasyon içermeyen) isteği atar.

Tekli mod kullanımı (bir grup: ya rastgele ya da tek sorgu):
    # Ortam değişkenleri (app.py ile aynı şema):
    export ELASTICSEARCH_URL="https://<deployment>.es.<region>.cloud.es.io"
    export ELASTICSEARCH_API_KEY="<api_key>"

    python evaluate_quality_sample.py --sample-size 200 --output report.jsonl
    python evaluate_quality_sample.py --query "gaming mouse" --sample-size 50 --output report.csv
    python evaluate_quality_sample.py --seed 42 --index amazon-products-000001

Çoklu grup modu (kalibrasyon için): rastgele bir örneklem + N adet sabit
sorgu grubunu TEK bir raporda birleştirir, her satırı hangi gruptan geldiğini
gösteren `query_group` alanıyla etiketler (bkz. CLAUDE.md kalite kalibrasyon
görevi §1):

    python evaluate_quality_sample.py \\
        --random-size 5000 --per-query-size 100 \\
        --output quality_sample_report_v3.csv

`--queries` verilmezse `DEFAULT_QUERY_GROUPS` kullanılır. `--random-size 0`
rastgele grubu, `--queries ""` sorgu gruplarını devre dışı bırakır.

Rapor alanları: parent_asin, title, main_category, source_category,
categories, title_family (title'dan çıkarılan kategori ailesi),
category_family (categories'ten çıkarılan aile),
title_category_consistency, data_quality_score, quality_flags,
matched_terms, conflicting_terms, query_group ("random" ya da sorgu metni).
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

RANDOM_GROUP_LABEL = "random"

DEFAULT_QUERY_GROUPS = [
    "gaming mouse",
    "wireless mouse",
    "mouse pad",
    "makeup brush",
    "dog food",
    "car phone holder",
    "book light",
    "gaming chair",
    "pet hair vacuum",
    "bluetooth headphones",
    "laptop stand",
    "brake pad",
    "lipstick",
    "office chair",
    "coffee grinder",
]

REPORT_FIELDS = [
    "query_group",
    "parent_asin",
    "title",
    "main_category",
    "source_category",
    "categories",
    "title_family",
    "category_family",
    "title_category_consistency",
    "data_quality_score",
    "quality_flags",
    "matched_terms",
    "conflicting_terms",
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
    parser.add_argument(
        "--random-size",
        type=int,
        default=None,
        help=(
            "Çoklu grup modu: rastgele örneklem boyutu (0 = rastgele grup devre dışı). "
            "Bu bayrak veya --queries verilirse çoklu grup modu aktif olur."
        ),
    )
    parser.add_argument(
        "--per-query-size",
        type=int,
        default=100,
        help="Çoklu grup modunda her sorgu grubu için belge sayısı (varsayılan: 100).",
    )
    parser.add_argument(
        "--queries",
        type=str,
        default=None,
        help=(
            "Çoklu grup modu: virgülle ayrılmış sorgu grupları listesi. "
            "Boş string ('') sorgu gruplarını devre dışı bırakır (yalnızca rastgele grup). "
            f"Verilmezse DEFAULT_QUERY_GROUPS ({len(DEFAULT_QUERY_GROUPS)} sorgu) kullanılır."
        ),
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
    Hiçbir yazma/güncelleme operatörü içermez.

    `operator: "and"` KASITLIDIR (app.py:search_config.json multi_match
    ayarıyla aynı) — varsayılan OR operatörü, çok kelimeli sorgularda
    (ör. "mouse pad") yalnızca TEK kelimeyi (ör. sadece "pad" — brake pad,
    knee pad...) içeren tamamen alakasız belgeleri de eşleştirip rastgele
    örnekleme havuzuna sokuyordu; bu da kalite kalibrasyon örneklemini
    anlamsız hâle getiriyordu."""
    base_query: dict[str, Any] = {"match_all": {}}
    if query_text:
        base_query = {
            "multi_match": {
                "query": query_text,
                "operator": "and",
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


def build_report_rows(hits: list[dict[str, Any]], query_group: str = RANDOM_GROUP_LABEL) -> list[dict[str, Any]]:
    rows = []
    for hit in hits:
        source = hit.get("_source", {}) or {}
        quality = evaluate_product_quality(source, include_explanation=True)
        explanation = quality.get("quality_explanation", {})
        signals = explanation.get("signals", {})
        rows.append({
            "query_group": query_group,
            "parent_asin": source.get("parent_asin") or hit.get("_id") or "",
            "title": source.get("title") or "",
            "main_category": source.get("main_category") or "",
            "source_category": source.get("source_category") or "",
            "categories": _categories_display(source),
            "title_family": signals.get("title_family"),
            "category_family": signals.get("category_family"),
            "title_category_consistency": quality["title_category_consistency"],
            "data_quality_score": quality["data_quality_score"],
            "quality_flags": quality["quality_flags"],
            "matched_terms": explanation.get("matched_terms", []),
            "conflicting_terms": explanation.get("conflicting_terms", []),
        })
    return rows


def build_sample_groups(
    random_size: int, queries: list[str], per_query_size: int
) -> list[tuple[str, str | None, int]]:
    """(query_group_label, query_text_or_None, size) üçlülerinin listesini üretir.
    Rastgele grup için query_text None'dır (build_sample_query -> match_all)."""
    groups: list[tuple[str, str | None, int]] = []
    if random_size > 0:
        groups.append((RANDOM_GROUP_LABEL, None, random_size))
    for query_text in queries:
        if query_text:
            groups.append((query_text, query_text, per_query_size))
    return groups


def fetch_all_groups(
    url: str,
    api_key: str,
    index: str,
    groups: list[tuple[str, str | None, int]],
    seed: int | None,
) -> list[dict[str, Any]]:
    """Her grup için AYRI bir read-only `_search` isteği atar (mutasyon yok)
    ve tüm satırları `query_group` etiketiyle tek listede birleştirir."""
    rows: list[dict[str, Any]] = []
    for label, query_text, size in groups:
        if size <= 0:
            continue
        payload = build_sample_query(query_text, size, seed)
        hits = fetch_sample(url, api_key, index, payload)
        rows.extend(build_report_rows(hits, query_group=label))
    return rows


def dedupe_rows_by_asin(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aynı `parent_asin` birden fazla grupta/gruplar arasında geldiyse
    analiz için tekilleştirir (ilk görülen satır tutulur)."""
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        asin = row.get("parent_asin")
        if asin:
            if asin in seen:
                continue
            seen.add(asin)
        result.append(row)
    return result


def write_report(rows: list[dict[str, Any]], output_path: Path) -> None:
    list_fields = ("quality_flags", "matched_terms", "conflicting_terms")
    if output_path.suffix.lower() == ".csv":
        with output_path.open("w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=REPORT_FIELDS)
            writer.writeheader()
            for row in rows:
                csv_row = dict(row)
                for field in list_fields:
                    csv_row[field] = ";".join(row.get(field) or [])
                writer.writerow(csv_row)
    else:
        with output_path.open("w", encoding="utf-8") as file:
            for row in rows:
                file.write(json.dumps(row, ensure_ascii=False) + "\n")


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    url, api_key = _require_credentials()

    multi_group_mode = args.random_size is not None or args.queries is not None
    if multi_group_mode:
        random_size = args.random_size if args.random_size is not None else 0
        if args.queries is None:
            queries = list(DEFAULT_QUERY_GROUPS)
        elif args.queries == "":
            queries = []
        else:
            queries = [q.strip() for q in args.queries.split(",") if q.strip()]

        groups = build_sample_groups(random_size, queries, args.per_query_size)
        if not groups:
            print("HATA: hiçbir grup tanımlanmadı (--random-size 0 ve --queries boş).", file=sys.stderr)
            sys.exit(1)
        rows = fetch_all_groups(url, api_key, args.index, groups, args.seed)
    else:
        payload = build_sample_query(args.query, args.sample_size, args.seed)
        hits = fetch_sample(url, api_key, args.index, payload)
        rows = build_report_rows(hits, query_group=args.query or RANDOM_GROUP_LABEL)

    if not rows:
        print("Örneklemde hiç belge bulunamadı.", file=sys.stderr)
        sys.exit(0)

    output_path = Path(args.output)
    write_report(rows, output_path)

    unique_rows = dedupe_rows_by_asin(rows)
    mismatch_count = sum(1 for row in rows if "title_category_mismatch" in row["quality_flags"])
    unique_mismatch_count = sum(1 for row in unique_rows if "title_category_mismatch" in row["quality_flags"])
    print(f"{len(rows)} belge değerlendirildi ({len(unique_rows)} benzersiz ASIN), rapor yazıldı: {output_path}")
    print(
        f"title_category_mismatch flag'i alan belge sayısı: {mismatch_count} "
        f"(benzersiz ASIN'lerde: {unique_mismatch_count})"
    )


if __name__ == "__main__":
    main()
