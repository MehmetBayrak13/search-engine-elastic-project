"""
`config/query_translations.json`nin kapsamadığı kategorileri bulan,
READ-ONLY, offline bir tarama aracı. Bu oturumda elle yapılan "cluster'dan
en yaygın kategorileri çek, sözlükle karşılaştır, eksikleri bul" sürecini
otomatikleştirir -- canlı sorguya hiç girmez, hiçbir çeviri/servis
çağrısı yapmaz, yalnızca zaten var olan sözlüğün İngilizce kelime
dağarcığıyla gerçek kategori isimlerini karşılaştırıp bir aday listesi
üretir. Nihai kararı (bir kategoriye gerçekten Türkçe karşılık eklenip
eklenmeyeceğini) hâlâ bir insan verir -- bu araç yalnızca "nereye bakmalı"
sorusunu hızlandırır.

Kullanım:
    export ELASTICSEARCH_URL="..."
    export ELASTICSEARCH_API_KEY="..."
    python tools/find_translation_gaps.py --size 400 --min-rating-number 10
    python tools/find_translation_gaps.py --output gaps.csv --format csv
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from services import search_service

_WORD_RE = re.compile(r"[a-zçğıöşü]+", re.IGNORECASE)

# Genel/kategori-yapısı kelimeleri -- kapsam hesaplamasında görmezden
# gelinir (ör. "accessories", "parts", "sets" tek başına anlamlı bir
# çeviri boşluğu göstermez, hemen her kategori adında geçer).
_STOPWORDS = {
    "and", "the", "for", "of", "with", "accessories", "accessory", "parts",
    "part", "supplies", "supply", "sets", "set", "kits", "kit", "products",
    "product", "tools", "tool", "equipment", "care", "other",
}


def build_covered_vocabulary() -> set[str]:
    """`query_translations.json` + `synonyms.json`nin ulaştığı TÜM İngilizce
    kelimeleri (çeviri hedefleri + eş anlamlı hedefleri) tek, düz bir küçük
    harf kelime kümesine indirger. Bir kategori kelimesi bu kümede varsa,
    onu bulacak BİR Türkçe sorgu zaten muhtemelen mevcuttur."""
    vocabulary: set[str] = set()
    translations = search_service.TRANSLATIONS
    synonyms = search_service.SYNONYMS
    if translations:
        for values in translations.phrases.values():
            for value in values:
                vocabulary.update(_WORD_RE.findall(value.lower()))
        for values in translations.terms.values():
            for value in values:
                vocabulary.update(_WORD_RE.findall(value.lower()))
    if synonyms:
        for values in synonyms.en_synonyms.values():
            for value in values:
                vocabulary.update(_WORD_RE.findall(value.lower()))
        # tr_redirects'in kendisi zaten terms/phrases'e yönlendiği için
        # ayrıca eklemeye gerek yok.
    return vocabulary


def fetch_top_categories(size: int, min_rating_number: int) -> list[tuple[str, int]]:
    """CLAUDE.md'deki veri-odaklı denetim yöntemiyle AYNI sorgu şekli:
    `rating_number > min_rating_number` filtresiyle en yaygın `categories`
    bucket'larını çeker (bkz. bu oturumda elle yapılan 200/500/750 kategori
    taramaları)."""
    payload = {
        "size": 0,
        "query": {"range": {"rating_number": {"gt": min_rating_number}}},
        "aggs": {"cats": {"terms": {"field": "categories", "size": size}}},
    }
    data, error = search_service._post_search(payload)
    if error:
        raise SystemExit(f"Elasticsearch hatası: {error}")
    buckets = data.get("aggregations", {}).get("cats", {}).get("buckets", [])
    return [(b["key"], b["doc_count"]) for b in buckets]


def compute_coverage(category: str, vocabulary: set[str]) -> tuple[float, list[str]]:
    words = [w for w in _WORD_RE.findall(category.lower()) if len(w) > 2 and w not in _STOPWORDS]
    if not words:
        return 1.0, []  # anlamlı kelimesi yoksa (ör. tek harf/stopword) atla, gap sayma
    uncovered = [w for w in words if w not in vocabulary]
    coverage = 1.0 - (len(uncovered) / len(words))
    return coverage, uncovered


def find_gaps(size: int, min_rating_number: int, max_coverage: float) -> list[dict]:
    vocabulary = build_covered_vocabulary()
    categories = fetch_top_categories(size, min_rating_number)
    gaps = []
    for category, doc_count in categories:
        coverage, uncovered = compute_coverage(category, vocabulary)
        if coverage <= max_coverage:
            gaps.append({
                "category": category,
                "doc_count": doc_count,
                "coverage": round(coverage, 2),
                "uncovered_words": ", ".join(uncovered),
            })
    gaps.sort(key=lambda row: row["doc_count"], reverse=True)
    return gaps


def write_report(rows: list[dict], output_path: Path, fmt: str) -> None:
    if fmt == "json":
        output_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
        return
    with output_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=["category", "doc_count", "coverage", "uncovered_words"])
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="config/query_translations.json'ın kapsamadığı kategorileri bulan "
        "READ-ONLY tarama aracı. Hiçbir çeviri servisine bağlanmaz, hiçbir belgeyi "
        "değiştirmez -- yalnızca _search aggregation istekleri atar."
    )
    parser.add_argument("--size", type=int, default=400, help="taranacak kategori bucket sayısı")
    parser.add_argument("--min-rating-number", type=int, default=10)
    parser.add_argument("--max-coverage", type=float, default=0.5,
                         help="bu eşiğin ALTINDAKİ (kapsanmayan kelime oranı yüksek) kategoriler raporlanır")
    parser.add_argument("--output", default="translation_gaps_report.json")
    parser.add_argument("--format", choices=["json", "csv"], default="json")
    args = parser.parse_args()

    gaps = find_gaps(args.size, args.min_rating_number, args.max_coverage)
    write_report(gaps, Path(args.output), args.format)
    print(f"{len(gaps)} olası boşluk bulundu (taranan {args.size} kategoriden), yazıldı: {args.output}")
    for row in gaps[:20]:
        print(f"  [{row['doc_count']:>7}] {row['category']!r:40} kapsam={row['coverage']}  kapsanmayan={row['uncovered_words']}")


if __name__ == "__main__":
    main()
