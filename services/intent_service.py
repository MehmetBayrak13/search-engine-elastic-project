"""
Kategori + title niyet (intent) sinyallerini SAF biçimde çözümler.

Bu modül Elasticsearch'e istek ATMAZ, Streamlit'e bağımlı DEĞİLDİR,
`session_state` kullanmaz. `services/search_service.py`, dinamik kategori
keşfinin ES aggregation çağrısını KENDİSİ yapar (I/O burada değil orada),
sonucu (`discovered_categories`) ve çeviri varyantlarını
(`translated_queries`) burada tanımlı `resolve_intent_signals`'a girdi
olarak verir. Bu modül yalnızca zaten elde edilmiş verileri birleştirir.

`positive_categories`/`negative_categories` çıktısı JSON-safe düz
sözlüklerden oluşur (bkz. IntentSignals) — bir HTTP debug response'una
doğrudan serileştirilebilir. `legacy_hard_exclusions` bunun dışındadır:
mevcut (bkz. §4 tasarım notu) `watch` kuralı gibi düz-string
`negative_categories`'in ÜRETTİĞİ, hazır Elasticsearch `must_not` madde
sözlükleridir — geriye dönük uyumluluk için AYNEN korunur, ama bir API/debug
response'una asla sızdırılmamalıdır (bkz. api/main.py: intent_debug).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from config import AppConfig, CategoryBoostEntry, CategoryPenaltyEntry, IntentRule, load_search_config

CONFIG: AppConfig = load_search_config()

__all__ = ["IntentSignals", "detect_search_intent", "resolve_intent_signals"]


@dataclass(frozen=True)
class IntentSignals:
    positive_categories: tuple[dict, ...] = ()
    negative_categories: tuple[dict, ...] = ()
    matched_rule_ids: tuple[str, ...] = ()
    legacy_hard_exclusions: tuple[dict, ...] = ()
    debug: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Terim eşleştirme (search_service.py'deki orijinal _contains_term ile aynı)
# ---------------------------------------------------------------------------
def _contains_term(text: str, term: str) -> bool:
    term = term.casefold()
    if " " in term:
        return term in text
    return re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text) is not None


def _term_in_any(term: str, haystacks: list[str]) -> bool:
    return any(_contains_term(haystack, term) for haystack in haystacks)


def _any_term_hits(terms: tuple[str, ...], haystacks: list[str]) -> bool:
    return any(_term_in_any(term, haystacks) for term in terms)


def _all_terms_hit(terms: tuple[str, ...], haystacks: list[str]) -> bool:
    return all(_term_in_any(term, haystacks) for term in terms)


def _match_rule(rule: IntentRule, haystacks: list[str]) -> dict | None:
    """Bir kuralın verilen haystack'lerde (orijinal + çevrilmiş sorgular)
    eşleşip eşleşmediğini değerlendirir. `all_terms` VE (varsa) etkin
    any-terms listesinin (yeni `any_terms`, yoksa legacy `query_terms`)
    ikisi de sağlanmalıdır. En az biri boş-olmayan tetikleyici listesi
    yoksa (config.py'de zaten engellenir ama savunma amaçlı) eşleşmez."""
    if not rule.enabled:
        return None

    effective_any = rule.any_terms or rule.query_terms
    if not (rule.all_terms or effective_any):
        return None
    if rule.all_terms and not _all_terms_hit(rule.all_terms, haystacks):
        return None
    if effective_any and not _any_term_hits(effective_any, haystacks):
        return None

    effective_excluded = rule.excluded_terms or rule.excluded_when_query_contains
    blocked = bool(effective_excluded) and _any_term_hits(effective_excluded, haystacks)
    return {"rule": rule, "apply_exclusion": not blocked}


def detect_search_intent(query_text: str, manual_rules: dict[str, IntentRule]) -> dict:
    """Tek bir niyet döner (UI rozeti için) — YALNIZCA orijinal sorguyu
    kullanır, ilk eşleşen kuralda durur. `resolve_intent_signals`'ın çoklu-
    kural birleştirme davranışından kasıtlı olarak farklıdır (bkz. modül
    docstring'i); bu, api/main.py'nin tek bir rozet göstermesi için yeterli
    ve bugünkü davranışla birebir aynıdır."""
    haystacks = [(query_text or "").casefold()]
    for name, rule in manual_rules.items():
        match = _match_rule(rule, haystacks)
        if match:
            return {"intent": name, "apply_exclusion": match["apply_exclusion"], "rule": match["rule"]}
    return {"intent": None, "apply_exclusion": False, "rule": None}


# ---------------------------------------------------------------------------
# Kategori eşleşme yardımcıları
# ---------------------------------------------------------------------------
def _legacy_hard_exclusion_clause(value: str) -> dict:
    return {
        "bool": {
            "should": [
                {"term": {"main_category": value}},
                {"term": {"source_category": value}},
                {"term": {"categories": value}},
                {"match_phrase": {"categories_text": {"query": value}}},
                {"match_phrase": {"categories_text.tr": {"query": value}}},
            ],
            "minimum_should_match": 1,
        }
    }


def _apply_cap(entries: list[dict], cap: float) -> list[dict]:
    """Scales `boost` (and, when present, `boost_tr` — legacy
    `category_boost_terms` entries carry both) down proportionally so their
    sum stays within `cap`. Scaling `boost_tr` by the same factor as `boost`
    keeps the categories_text/categories_text.tr ratio intact under capping
    instead of leaving `boost_tr` un-scaled."""
    total = sum(entry["boost"] for entry in entries)
    if total <= cap or total <= 0:
        return entries
    scale = cap / total
    scaled: list[dict] = []
    for entry in entries:
        new_entry = {**entry, "boost": round(entry["boost"] * scale, 6)}
        if "boost_tr" in new_entry:
            new_entry["boost_tr"] = round(new_entry["boost_tr"] * scale, 6)
        scaled.append(new_entry)
    return scaled


def _apply_penalty_floor(entries: list[dict], floor: float) -> list[dict]:
    return [{**entry, "penalty": max(entry["penalty"], floor)} for entry in entries]


def resolve_intent_signals(
    query: str,
    translated_queries: list[str],
    manual_rules: dict[str, IntentRule],
    discovered_categories: list[dict],
    *,
    config: AppConfig | None = None,
) -> IntentSignals:
    cfg = config or CONFIG
    ranking = cfg.intent_ranking

    haystacks = [(query or "").casefold()] + [(t or "").casefold() for t in translated_queries]

    matched_rule_ids: list[str] = []
    manual_positive: list[dict] = []
    soft_negative: list[dict] = []
    legacy_hard_exclusions: list[dict] = []

    for name, rule in manual_rules.items():
        match = _match_rule(rule, haystacks)
        if not match:
            continue
        matched_rule_ids.append(name)

        for term in rule.category_boost_terms:
            # Legacy `category_boost_terms` entries carry BOTH boosts through
            # (`boost` for categories_text, `boost_tr` for categories_text.tr)
            # — pre-branch behavior applied two DIFFERENT boost values here
            # (12/8 for the `watch` rule). `_positive_category_should_clauses`
            # reads `boost_tr` when present; new-schema `positive_categories`
            # entries below deliberately omit it, so they keep the single
            # shared-boost behavior spec'd for that field (see design §3).
            manual_positive.append({
                "value": term,
                "boost": rule.category_boost,
                "boost_tr": rule.category_boost_tr,
                "source": "manual",
                "field": "categories_text",
            })
        for entry in rule.positive_categories:
            manual_positive.append({
                "value": entry.value, "boost": entry.boost, "source": "manual", "field": "categories_text",
            })

        if match["apply_exclusion"]:
            for value in rule.negative_categories:
                legacy_hard_exclusions.append(_legacy_hard_exclusion_clause(value))
            for entry in rule.soft_negative_categories:
                soft_negative.append({"value": entry.value, "penalty": entry.penalty, "source": "manual"})

    manual_positive = _apply_cap(manual_positive, ranking.manual_category_boost_cap) if ranking.enabled else manual_positive
    soft_negative = (
        _apply_penalty_floor(soft_negative, ranking.negative_penalty_floor) if ranking.enabled else soft_negative
    )

    blocked_dynamic_values = {v.casefold() for name in matched_rule_ids for v in manual_rules[name].negative_categories}
    dynamic_positive: list[dict] = []
    seen_dynamic: set[tuple[str, str]] = set()
    for candidate in discovered_categories:
        if str(candidate["value"]).casefold() in blocked_dynamic_values:
            continue
        key = (candidate["field"], candidate["value"])
        if key in seen_dynamic:
            continue
        seen_dynamic.add(key)
        dynamic_positive.append({
            "value": candidate["value"],
            "field": candidate["field"],
            "boost": cfg.dynamic_intent.boost,
            "source": "dynamic",
        })
    dynamic_positive = (
        _apply_cap(dynamic_positive, ranking.dynamic_category_boost_cap) if ranking.enabled else dynamic_positive
    )

    return IntentSignals(
        positive_categories=tuple(manual_positive + dynamic_positive),
        negative_categories=tuple(soft_negative),
        matched_rule_ids=tuple(matched_rule_ids),
        legacy_hard_exclusions=tuple(legacy_hard_exclusions),
        debug={"dynamic_discovery": list(discovered_categories), "translated_queries_used": list(translated_queries)},
    )
