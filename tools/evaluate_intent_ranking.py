"""
Kategori + title intent ranking için offline, READ-ONLY before/after
değerlendirme raporu. Gerçek Elastic Cloud'a yalnızca `_search` istekleri
atar (search_service.search_products üzerinden) — hiçbir belgeyi
indexlemez/günceller/silmez.

"Before"/"after" karşılaştırması, process içinde CONFIG'i MUTATE ETMEDEN,
iki bağımsız AppConfig nesnesi (bkz. search_service fonksiyonlarındaki
`config=` override parametresi) arasında yapılır — before/after asla aynı
mutable state'i paylaşmaz, bu yüzden lru_cache kaynaklı yanlış sonuç riski
yoktur (bkz. spec §10).

Kullanım:
    export ELASTICSEARCH_URL="..."
    export ELASTICSEARCH_API_KEY="..."
    python tools/evaluate_intent_ranking.py --output report.json
    python tools/evaluate_intent_ranking.py --output report.csv --format csv
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import replace
from pathlib import Path

# `tools/` bir alt dizin olduğu için `python tools/evaluate_intent_ranking.py`
# ile doğrudan çalıştırıldığında sys.path[0] repo kökü değil `tools/` olur.
# Repo kökünü ekleyerek hem `python tools/evaluate_intent_ranking.py` hem de
# `python -m tools.evaluate_intent_ranking` / pytest içinden import çalışsın.
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from config import AppConfig, load_search_config
from services import search_service

DEFAULT_QUERIES = [
    "wireless mouse", "iphone case", "running shoes", "gaming keyboard",
    "coffee maker", "dog food", "laptop stand", "usb c cable",
    "phone charger", "bluetooth headphones",
]

REPORT_FIELDS = ["query", "variant", "rank", "title", "category", "score", "matched_rule", "discovered_category"]


def build_variant_configs() -> tuple[AppConfig, AppConfig]:
    """"before" = title_ranking/intent_ranking devre dışı (bugünkü davranış),
    "after" = repodaki gerçek search_config.json (yeni özellikler açık).
    İkisi de `load_search_config()`'ün döndürdüğü AYRI, immutable AppConfig
    nesneleridir — hiçbiri paylaşılan global state'i değiştirmez."""
    after = load_search_config()
    before = replace(
        after,
        title_ranking=replace(after.title_ranking, enabled=False),
        intent_ranking=replace(after.intent_ranking, enabled=False),
    )
    return before, after


def run_variant(query: str, variant_config: AppConfig, label: str) -> list[dict]:
    """Yalnızca `search_service.search_products` çağırır (read-only `_search`
    isteği) — başka hiçbir Elasticsearch işlemi tetiklemez."""
    result = search_service.search_products(query, config=variant_config)
    rows = []
    for rank, hit in enumerate(result.hits or [], start=1):
        source = hit.get("_source", {})
        rows.append({
            "query": query,
            "variant": label,
            "rank": rank,
            "title": source.get("title", ""),
            "category": source.get("main_category", ""),
            "score": hit.get("_score", 0),
            "matched_rule": None,
            "discovered_category": None,
        })
    return rows


def write_report(rows: list[dict], output_path: Path, fmt: str) -> None:
    if fmt == "json":
        output_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
        return
    with output_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=REPORT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="READ-ONLY before/after intent ranking evaluator. "
        "Sadece Elasticsearch'e _search istekleri atar; hiçbir belgeyi "
        "indexlemez/günceller/silmez.",
    )
    parser.add_argument("--output", default="intent_ranking_report.json")
    parser.add_argument("--format", choices=["json", "csv"], default="json")
    parser.add_argument("--queries", nargs="*", default=None)
    args = parser.parse_args()

    queries = args.queries if args.queries else DEFAULT_QUERIES
    before, after = build_variant_configs()

    rows: list[dict] = []
    for query in queries:
        rows.extend(run_variant(query, before, "before"))
        rows.extend(run_variant(query, after, "after"))

    write_report(rows, Path(args.output), args.format)
    print(f"{len(rows)} satır yazıldı: {args.output}")


if __name__ == "__main__":
    main()
