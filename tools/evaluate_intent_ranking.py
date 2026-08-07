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

ÖNEMLİ — "before"/"after" ETİKETLERİ NE ANLAMA GELİR (final review Finding
2, bkz. build_variant_configs docstring'i): `intent_ranking.enabled=False`
yalnızca cap/floor SKALALAMA adımını (`_apply_cap`/`_apply_penalty_floor`)
atlar; `positive_categories`/soft `negative_categories`/`boosting`
sarmalayıcısı bu bayraktan BAĞIMSIZ olarak yine tam olarak tetiklenir —
sadece sınırsız (uncapped/unfloored) kalır. Yani "before" özelliğin
TAMAMEN kapalı olduğu, gerçek bir dal-öncesi (pre-branch) durum DEĞİLDİR;
"before" = yeni manuel/dinamik kategori sinyalleri AKTİF ama cap/floor
UYGULANMAMIŞ (aşırı bir boost değeri küçültülmeden kalabilir), "after" =
aynı sinyaller cap/floor UYGULANMIŞ haliyle. `intent_rules.json`'daki
manuel kural seti (`INTENT_RULES`, modül-global yüklenir) HER İKİ
varyantta da AYNIDIR — config override'ı `INTENT_RULES`'ı etkilemez, yalnızca
`title_ranking.enabled` (gerçek, temiz bir açık/kapalı) ve cap/floor
skalalamasını değiştirir.

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
    # `smartwatch` is in the `watch` rule's `query_terms` (config/intent_rules.json)
    # so it reliably triggers manual category_boost_terms — added for final
    # review Finding 3 to surface manual_category_boost_cap's effect on the
    # watch rule's category_boost (12) / category_boost_tr (8) signal.
    "smartwatch",
    # Field consensus/contradiction acceptance queries (bkz. spec §10/§12 —
    # `men perfume` is the worked false-positive example: title matches but
    # description/features/category should disagree once contradiction_terms
    # fires; `face moisturizer`/`gaming mouse` are the corresponding
    # should-rank-well counterparts).
    "men perfume", "face moisturizer", "gaming mouse",
]

REPORT_FIELDS = [
    "query", "variant", "rank", "title", "category", "score", "matched_rule", "discovered_category",
    "matched_fields", "consensus_level", "contradictions", "applied_penalty",
]


def build_variant_configs() -> tuple[AppConfig, AppConfig]:
    """"before" = `title_ranking.enabled=False` (gerçek, temiz bir açık/kapalı
    — title tier'ları hiç eklenmez) VE `intent_ranking.enabled=False`. Bu
    ikinci bayrak YANILTICI olabilir: yalnızca cap/floor SKALALAMASINI
    (`_apply_cap`/`_apply_penalty_floor`) atlar — manuel (`intent_rules.json`)
    ve dinamik kategori sinyalleri, soft negatif kategoriler ve `boosting`
    sarmalayıcısı yine tam olarak tetiklenir, sadece SINIRSIZ (uncapped/
    unfloored) kalır. Yani "before" gerçek bir dal-öncesi/özellik-kapalı
    durum DEĞİL, "yeni sinyaller açık ama sınırlanmamış" durumudur — aşırı
    bir manuel boost değeri (örn. `watch` kuralının toplam 72'lik
    category_boost_terms toplamı) "before"da hiç küçültülmeden görünebilir,
    bu da "after"dan (cap uygulanmış) daha agresif/uç görünmesine yol
    açabilir. "after" = repodaki gerçek search_config.json (title_ranking
    açık, cap/floor uygulanmış). `intent_rules.json`'daki manuel kural seti
    (`INTENT_RULES`) HER İKİ varyantta da AYNIDIR — bu config override'ıyla
    değişmez (bkz. final review Finding 2, modül docstring'i). İkisi de
    `load_search_config()`'ün döndürdüğü AYRI, immutable AppConfig
    nesneleridir — hiçbiri paylaşılan global state'i değiştirmez."""
    after = load_search_config()
    before = replace(
        after,
        title_ranking=replace(after.title_ranking, enabled=False),
        intent_ranking=replace(after.intent_ranking, enabled=False),
    )
    return before, after


def build_field_relevance_variant_configs() -> tuple[AppConfig, AppConfig]:
    """Bu YENİ özelliğin (field_relevance/field_consensus/relevance_contradiction)
    KENDİ izole açık/kapalı temeli — `build_variant_configs()`ın (title_ranking/
    intent_ranking) YERİNE değil, YANINA eklenir; ikisi ayrı, bağımsız
    before/after çiftleridir. "field_before" = repodaki gerçek config,
    yalnızca field_relevance/field_consensus/relevance_contradiction KAPALI
    (title_ranking/intent_ranking AÇIK kalır — bu, bu üç yeni bayrağın
    KENDİ artımlı etkisini izole eder). "field_after" = repodaki gerçek
    config, değişmeden (üçü de açık). `build_variant_configs()`ın kendi
    docstring'inde zaten belgelendiği gibi ("before" tam bir dal-öncesi
    durum değil, yalnızca izole edilmiş bir bayrak karşılaştırmasıdır) bu
    fonksiyon da aynı pragmatik yaklaşımı izler: "field_before" TAM bir
    bu-özellik-öncesi tarihsel duruma denk gelmez (multi_match zaten
    field_relevance ile değiştirildiği için "field_before" yalnızca
    exact_asin/phrase/title_ranking-exact-prefix/fuzzy'e dayanır, eski
    dis_max multi_match'e DEĞİL) — yine de bu üç bayrağın izole artımlı
    etkisini doğru şekilde gösterir."""
    after = load_search_config()
    before = replace(
        after,
        field_relevance=replace(after.field_relevance, enabled=False),
        field_consensus=replace(after.field_consensus, enabled=False),
        relevance_contradiction=replace(after.relevance_contradiction, enabled=False),
    )
    return before, after


def run_variant(query: str, variant_config: AppConfig, label: str) -> list[dict]:
    """Yalnızca `search_service.search_products` çağırır (read-only `_search`
    isteği) — başka hiçbir Elasticsearch işlemi tetiklemez.
    `include_relevance_debug=True` ES'e EK bir istek YAPTIRMAZ (bkz.
    `relevance_debug_from_matched_queries` docstring'i) — yalnızca sorguya
    `_name` ekler, aynı `_search` yanıtından `matched_queries` okunur."""
    result = search_service.search_products(query, config=variant_config, include_relevance_debug=True)
    rows = []
    for rank, hit in enumerate(result.hits or [], start=1):
        source = hit.get("_source", {})
        debug = search_service.relevance_debug_from_matched_queries(hit.get("matched_queries"), variant_config)
        rows.append({
            "query": query,
            "variant": label,
            "rank": rank,
            "title": source.get("title", ""),
            "category": source.get("main_category", ""),
            "score": hit.get("_score", 0),
            "matched_rule": None,
            "discovered_category": None,
            "matched_fields": "|".join(debug["matched_fields"]),
            "consensus_level": debug["consensus_level"],
            "contradictions": "|".join(debug["contradictions"]),
            "applied_penalty": debug["applied_penalty"],
        })
    return rows


def summarize_rank_deltas(rows: list[dict], query: str, before_label: str, after_label: str) -> list[dict]:
    """`query` için `before_label`/`after_label` varyantları arasında,
    title bazında rank farkını hesaplar; en büyük düşüşten (kötüleşen rank)
    en büyük yükselişe doğru sıralar — spec §10'un "All Beauty yanlış
    eşleşmelerin neden düştüğünü göster" gereksinimi için. Bir title
    `after_label` varyantında hiç görünmüyorsa (üst N sonuçtan tamamen
    düştüyse) `after_rank`/`rank_delta` `None` döner ve bu, sıralamada
    EN BÜYÜK düşüş olarak ele alınır (herhangi bir sonlu delta'dan daha
    kötü kabul edilir)."""
    before_ranks = {
        row["title"]: row["rank"] for row in rows if row["query"] == query and row["variant"] == before_label
    }
    after_ranks = {
        row["title"]: row["rank"] for row in rows if row["query"] == query and row["variant"] == after_label
    }
    deltas = []
    for title, before_rank in before_ranks.items():
        after_rank = after_ranks.get(title)
        rank_delta = (after_rank - before_rank) if after_rank is not None else None
        deltas.append({"title": title, "before_rank": before_rank, "after_rank": after_rank, "rank_delta": rank_delta})

    def _effective_delta(entry: dict) -> int:
        return entry["rank_delta"] if entry["rank_delta"] is not None else 10_000

    deltas.sort(key=_effective_delta, reverse=True)
    return deltas


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
    field_before, field_after = build_field_relevance_variant_configs()

    rows: list[dict] = []
    for query in queries:
        rows.extend(run_variant(query, before, "before"))
        rows.extend(run_variant(query, after, "after"))
        rows.extend(run_variant(query, field_before, "field_before"))
        rows.extend(run_variant(query, field_after, "field_after"))

    write_report(rows, Path(args.output), args.format)
    print(f"{len(rows)} satır yazıldı: {args.output}")

    if "men perfume" in queries:
        print("\n'men perfume' field_before -> field_after en büyük rank düşüşleri:")
        for entry in summarize_rank_deltas(rows, "men perfume", "field_before", "field_after")[:10]:
            print(f"  {entry['title']!r}: {entry['before_rank']} -> {entry['after_rank']} (delta={entry['rank_delta']})")


if __name__ == "__main__":
    main()
