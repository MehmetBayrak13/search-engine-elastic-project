"""
Ana ürün araması servis katmanı: intent tespiti, Türkçe->İngilizce sorgu
genişletme, dinamik kategori keşfi, kalite reranking ve Elasticsearch sorgu
oluşturma/çalıştırma burada yaşar.

Bu modül Streamlit'e bağımlı DEĞİLDİR: `import streamlit` yoktur,
`session_state` kullanılmaz, önbellekleme (`st.cache_data`) burada değil
çağıran UI katmanında (`app.py`) yapılır — bu yüzden aşağıdaki fetch
fonksiyonları `fetch_aggregations`/benzeri bir DI (dependency injection)
parametresi kabul eder: varsayılanı önbelleksiz gerçek Elasticsearch çağrısıdır,
`app.py` kendi `st.cache_data` sarmalayıcısını enjekte ederek AYNI önbellekleme
davranışını (TTL, cache key) korur. Testler ve ileride bir FastAPI endpoint'i
bu modülü Streamlit'siz doğrudan çağırabilir.

`_post_search`, testlerin `monkeypatch.setattr(search_service, "_post_search", ...)`
ile mock'layabilmesi için modül seviyesinde bir isim olarak kalır;
`autocomplete_service` bu fonksiyonu KENDİ isim alanına kopyalamadan
(`from ... import _post_search` DEĞİL), modül referansıyla
(`search_service._post_search(...)`) çağırır — aksi halde monkeypatch bu
modülü değiştirdiğinde `autocomplete_service`'teki eski kopya etkilenmez.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

import requests

from config import ConfigError, load_intent_rules, load_search_config, load_translations
from services import intent_service
from services.intent_service import IntentSignals
from services.search_models import PaginationLimitError, SearchResult

if TYPE_CHECKING:
    from config import AppConfig

__all__ = [
    "CONFIG",
    "CONFIG_ERROR",
    "TRANSLATIONS",
    "INTENT_RULES",
    "ES_URL",
    "ES_API_KEY",
    "INDEX_NAME",
    "SOURCE_FIELDS",
    "PaginationLimitError",
    "SearchResult",
    "detect_search_intent",
    "expand_multilingual_query",
    "build_category_discovery_query",
    "fetch_category_aggregations",
    "discover_category_intent",
    "resolve_intent_signals",
    "build_search_query",
    "search_products",
    "relevance_debug_from_matched_queries",
]

# ---------------------------------------------------------------------------
# Yapılandırma yükleme (app.py'deki UI-yükleme akışından bağımsız kendi kopyası
# — config.load_search_config @lru_cache'li olduğundan aynı AppConfig nesnesini
# döner, iki modül arasında veri sapması olmaz).
# ---------------------------------------------------------------------------
ES_URL = os.getenv("ELASTICSEARCH_URL")
ES_API_KEY = os.getenv("ELASTICSEARCH_API_KEY")
if ES_URL:
    ES_URL = ES_URL.rstrip("/")

CONFIG_ERROR: str | None = None
try:
    CONFIG = load_search_config()
    INTENT_RULES = load_intent_rules()
    TRANSLATIONS = load_translations()
except ConfigError as error:
    CONFIG = None
    INTENT_RULES = {}
    TRANSLATIONS = None
    CONFIG_ERROR = str(error)

INDEX_NAME = CONFIG.elasticsearch.search_index_expr if CONFIG else ""
SOURCE_FIELDS = list(CONFIG.source_fields.search) if CONFIG else []


# ---------------------------------------------------------------------------
# Niyet (intent) tespiti — kategori farkındalıklı reranking
# ---------------------------------------------------------------------------
# Kurallar config/intent_rules.json'dan yüklenir (INTENT_RULES). Bu, kolay
# genişletilebilir bir yapı sağlar: yeni bir intent eklemek için kod
# değiştirmeye gerek yoktur, JSON dosyasına yeni bir kural eklemek yeterlidir.
def detect_search_intent(query_text: str) -> dict:
    """UI rozeti için tek niyet döner — bkz. intent_service.detect_search_intent
    docstring'i (yalnızca orijinal sorgu, ilk eşleşen kuralda durur)."""
    return intent_service.detect_search_intent(query_text, INTENT_RULES)


# ---------------------------------------------------------------------------
# Türkçe → İngilizce sorgu genişletme (çok-dilli arama)
# ---------------------------------------------------------------------------
def _normalize_query_text(query_text: str) -> str:
    """Casefold + tekrarlı boşlukları sadeleştirir (gerçek Türkçe stemming
    Elasticsearch analyzer'ında yapılır; burada yalnızca sözlük araması için
    normalize edilir)."""
    return " ".join((query_text or "").casefold().split())


def expand_multilingual_query(query_text: str, translations=None) -> dict:
    """
    Türkçe bir sorguyu, config/query_translations.json sözlüğünü kullanarak
    olası İngilizce karşılıklarına genişletir. Elasticsearch'e istek atmaz —
    saf (pure) ve test edilebilir bir fonksiyondur.

    Öncelik: tam ifade çevirisi > kelime bazlı çeviri. Sözlükte eşleşme
    yoksa yalnızca orijinal sorgu ile devam edilir (fallback).

    Dönüş:
      {
        "original_query": str,
        "normalized_query": str,
        "phrase_translations": [str, ...],
        "token_translations": [str, ...],
        "expanded_queries": [str, ...],
        "used_translation": bool,
      }
    """
    translations = translations if translations is not None else TRANSLATIONS
    original = query_text or ""
    normalized = _normalize_query_text(original)

    max_variants = CONFIG.translation.max_variants if CONFIG else 3

    phrase_translations: list[str] = []
    token_translations: list[str] = []

    if translations and normalized:
        phrase_hits = translations.phrases.get(normalized)
        if phrase_hits:
            for variant in phrase_hits:
                if variant not in phrase_translations:
                    phrase_translations.append(variant)
                if len(phrase_translations) >= max_variants:
                    break

        # Kelime bazlı çeviri yalnızca tam ifade eşleşmesi yoksa denenir.
        if not phrase_translations:
            for token in normalized.split():
                for variant in translations.terms.get(token, ()):
                    if variant not in token_translations:
                        token_translations.append(variant)
                if len(token_translations) >= max_variants:
                    break
            token_translations = token_translations[:max_variants]

    expanded_queries = [original]
    if normalized and normalized != original.casefold():
        expanded_queries.append(normalized)
    for variant in phrase_translations + token_translations:
        if variant not in expanded_queries:
            expanded_queries.append(variant)

    return {
        "original_query": original,
        "normalized_query": normalized,
        "phrase_translations": phrase_translations,
        "token_translations": token_translations,
        "expanded_queries": expanded_queries,
        "used_translation": bool(phrase_translations or token_translations),
    }


def _build_translation_lexical_queries(query_text: str) -> list[dict]:
    """
    Çeviri sözlüğünden üretilen İngilizce alternatifleri, zorunlu lexical
    eşleşme grubuna (bool.must içindeki bool.should) EK seçenekler olarak
    ekler. Böylece salt Türkçe bir sorgu, İngilizce ürün kataloğunda da
    sonuç bulabilir — çeviri sinyali zorunlu eşleşmeyi atlamaz, ona bir
    alternatif ekler.
    """
    if not CONFIG or not CONFIG.translation.enabled:
        return []
    if len(_normalize_query_text(query_text)) < CONFIG.translation.min_query_length:
        return []

    expansion = expand_multilingual_query(query_text)
    queries: list[dict] = []

    phrase_field = CONFIG.search_methods.phrase.field
    for phrase in expansion["phrase_translations"]:
        queries.append({
            "match_phrase": {
                phrase_field: {"query": phrase, "boost": CONFIG.translation.phrase_boost}
            }
        })

    if expansion["token_translations"]:
        queries.append({
            "multi_match": {
                "query": " ".join(expansion["token_translations"]),
                "type": "best_fields",
                # operator eksikse ES varsayılanı "or" olur — tek bir çeviri
                # token'ının (ör. yalnızca "wireless") herhangi bir alanda tek
                # başına eşleşmesi zorunlu lexical kapıyı geçirir (bkz. fuzzy
                # multi_match'teki aynı sınıf hata, search_service.py D bloğu).
                "operator": CONFIG.field_relevance.operator,
                "boost": CONFIG.translation.token_boost,
                # `search_methods.multi_match` kaldırıldı (bkz. Task 1) — alan
                # listesi artık aynı amaca hizmet eden `field_relevance`'tan
                # gelir (title/features/description/categories_text + .tr).
                "fields": CONFIG.field_relevance.es_fields,
            }
        })

    return queries


# ---------------------------------------------------------------------------
# Dinamik kategori keşfi (Elasticsearch aggregation tabanlı)
# ---------------------------------------------------------------------------
# intent_rules.json artık ana intent motoru DEĞİLDİR — burası ana motordur.
# intent_rules.json, bunun üzerine binen opsiyonel bir override katmanıdır
# (alias/force-boost/exclusion/display-label/icon/priority/enabled). Bu
# katman tamamen boş ({}) olsa da kategori keşfi kesintisiz çalışır.
def build_category_discovery_query(
    query_text: str,
    extra_query_texts: list[str] | None = None,
    *,
    config: "AppConfig | None" = None,
) -> dict:
    """
    Yalnızca kategori adaylarını keşfetmek için size=0 bir aggregation
    sorgusu üretir. Ürün döndürmez; `aggregation_fields` (config'ten) üzerinde
    terms aggregation çalıştırır. `extra_query_texts` ile normalize edilmiş
    sorgu ve/veya en yüksek öncelikli çeviri de eşleşme havuzuna eklenebilir.

    Kullanıcıdan ham alan adı veya ham Query DSL alınmaz; tüm alanlar ve
    limitler config/search_config.json'daki `dynamic_intent` bölümünden gelir.

    `config`: opsiyonel `AppConfig` override'ı (varsayılan: modül seviyesi
    `CONFIG`). Yalnızca `tools/evaluate_intent_ranking.py` gibi offline
    değerlendirme araçlarının, global state'i değiştirmeden aynı süreçte
    birden fazla config varyantını karşılaştırabilmesi için vardır; normal
    çağrı yollarında (app.py, api) her zaman atlanır.
    """
    cfg = config or CONFIG
    dyn = cfg.dynamic_intent

    candidate_texts = [text for text in [query_text, *(extra_query_texts or [])] if text]
    should = [
        {
            "multi_match": {
                "query": text,
                "type": "best_fields",
                # operator eksikse ES varsayılanı "or" olur -- çok kelimeli
                # bir sorgunun (ör. "wireless headphones") TEK bir kelimesi
                # (ör. yalnızca "wireless") herhangi bir alanda geçen HER
                # ürünü aggregation örneklemine dahil eder ve kategori
                # keşfini kirletir (bkz. fuzzy/çeviri multi_match'lerindeki
                # aynı sınıf hata, search_service.py D bloğu ve
                # _build_translation_lexical_queries).
                "operator": cfg.field_relevance.operator,
                "fields": dyn.es_search_fields,
            }
        }
        for text in candidate_texts
    ]

    aggs = {
        agg_name: {"terms": {"field": field, "size": dyn.aggregation_size}}
        for agg_name, field in dyn.aggregation_bucket_map.items()
    }

    query = {"bool": {"should": should, "minimum_should_match": 1}} if should else {"match_all": {}}

    # Kategori keşfi, lexical olarak eşleşen ama düşük veri kaliteli
    # belgelerden (ör. title-category mismatch) yanlış kategori adayı
    # öğrenebilir (bkz. CLAUDE.md §8). `quality_ranking.discovery_filter_enabled`
    # açıkken bu belgeler aggregation örnekleminden dışlanır. Eski
    # index'lerde data_quality_score alanı yoksa bu range sorgusu hiçbir
    # belgeyle eşleşmez → must_not altında hiçbir şeyi dışlamaz (no-op);
    # bu yüzden reindex tamamlanana kadar flag açık bırakılsa bile davranış
    # bozulmaz. Yine de ilk migration öncesinde flag KAPALI tutulur (varsayılan).
    qr = cfg.quality_ranking
    if qr.discovery_filter_enabled:
        query = {
            "bool": {
                "must": [query],
                "must_not": [{"range": {qr.score_field: {"lt": qr.discovery_min_data_quality_score}}}],
            }
        }

    return {
        "size": 0,
        "track_total_hits": False,
        "timeout": f"{dyn.timeout_seconds}s",
        "query": query,
        "aggs": aggs,
    }


def fetch_category_aggregations(
    query_text: str, extra_query_texts: tuple[str, ...] = (), *, config: "AppConfig | None" = None
):
    """
    Kategori keşif aggregation'ını Elasticsearch'ten getirir (ÖNBELLEKSİZ —
    Streamlit önbelleklemesi burada değil `app.py`'de yapılır, bkz. modül
    docstring'i). `discover_category_intent`'in varsayılan fetcher'ıdır;
    çağıran taraf (app.py) kendi `st.cache_data` sarmalayıcısını enjekte
    edebilir.

    `config`: opsiyonel `AppConfig` override'ı (bkz. `build_category_discovery_query`).

    Dönüş: (aggregations_dict, hata_mesajı). Hata varsa aggregations_dict {}
    döner — bu, çağıran tarafın hatayı yutup normal aramayı engellememesini
    sağlar; kategori keşfi tek hata noktası olamaz.
    """
    cfg = config or CONFIG
    payload = build_category_discovery_query(query_text, list(extra_query_texts), config=cfg)
    data, error = _post_search(
        payload, timeout=cfg.dynamic_intent.timeout_seconds, index=cfg.elasticsearch.search_index_expr
    )
    if error:
        return {}, error
    return data.get("aggregations", {}), None


def discover_category_intent(
    query_text: str, *, fetch_aggregations=None, config: "AppConfig | None" = None
) -> list[dict]:
    """
    Sorgudan (orijinal + normalize edilmiş + en yüksek öncelikli İngilizce
    çeviri kullanılarak) Elasticsearch aggregation'ları ile kategori adayları
    keşfeder. Önceden `intent_rules.json`da tanımlanmamış tamamen yeni ürün
    tipleri için de (ör. "toilet paper", "gaming mouse", "cat food") çalışır.

    Başarısız olursa (timeout, bağlantı hatası, min. sorgu uzunluğu altında,
    devre dışı) sessizce boş liste döner — normal aramayı ASLA engellemez.

    `fetch_aggregations`: varsayılan `fetch_category_aggregations` (önbelleksiz);
    `app.py` burada kendi `st.cache_data` sarmalayıcısını geçirir.
    `config`: opsiyonel `AppConfig` override'ı (bkz. `build_category_discovery_query`);
    varsayılan fetcher'a da aktarılır.

    Dönüş: [{"value": str, "field": str, "doc_count": int, "rank": int,
             "source": "dynamic_category_discovery"}, ...]
    """
    cfg = config or CONFIG
    if fetch_aggregations is None:
        fetch_aggregations = lambda q, extra: fetch_category_aggregations(q, extra, config=cfg)

    dyn = cfg.dynamic_intent
    if not dyn.enabled:
        return []

    normalized = _normalize_query_text(query_text)
    if len(normalized) < dyn.minimum_query_length:
        return []

    expansion = expand_multilingual_query(query_text)
    extra_texts: list[str] = []
    if expansion["normalized_query"] and expansion["normalized_query"] != expansion["original_query"]:
        extra_texts.append(expansion["normalized_query"])

    top_translations = expansion["phrase_translations"] or expansion["token_translations"]
    if top_translations:
        extra_texts.append(top_translations[0])

    aggregations, error = fetch_aggregations(query_text, tuple(extra_texts))
    if error or not aggregations:
        return []

    candidates: list[dict] = []
    for agg_name, field in dyn.aggregation_bucket_map.items():
        buckets = aggregations.get(agg_name, {}).get("buckets", [])
        for rank, bucket in enumerate(buckets, start=1):
            value = bucket.get("key")
            if not value:
                continue
            candidates.append({
                "value": value,
                "field": field,
                "doc_count": bucket.get("doc_count", 0),
                "rank": rank,
                "source": "dynamic_category_discovery",
            })

    candidates.sort(key=lambda item: item["doc_count"], reverse=True)
    return candidates[: dyn.max_category_candidates]


def resolve_intent_signals(
    query_text: str,
    include_dynamic: bool = True,
    *,
    fetch_aggregations=None,
    config: "AppConfig | None" = None,
) -> IntentSignals:
    """Manuel kurallar + dinamik kategori keşfini birleştiren ana giriş
    noktası. Çeviri genişletmesi ve dinamik keşfin ES aggregation çağrısı
    (I/O) burada yapılır; SAF birleştirme mantığı `intent_service`'e
    devredilir (bkz. modül docstring'i)."""
    cfg = config or CONFIG
    expansion = expand_multilingual_query(query_text)
    translated_queries = expansion["phrase_translations"] + expansion["token_translations"]
    if expansion["normalized_query"] and expansion["normalized_query"] != expansion["original_query"]:
        translated_queries = [expansion["normalized_query"]] + translated_queries

    discovered_categories: list[dict] = []
    if include_dynamic and cfg.dynamic_intent.enabled:
        discovered_categories = discover_category_intent(
            query_text, fetch_aggregations=fetch_aggregations, config=cfg
        )

    return intent_service.resolve_intent_signals(
        query_text, translated_queries, INTENT_RULES, discovered_categories, config=cfg
    )


# ---------------------------------------------------------------------------
# Ürün veri kalitesi (product_quality.py) sinyalleri — yalnızca reranking
# ---------------------------------------------------------------------------
# `quality_ranking.enabled=false` iken (varsayılan — mevcut production
# index'lerinde title_category_consistency/data_quality_score alanları henüz
# YOK) bu bölüm hiçbir şeyi değiştirmez. Etkinleştirildiğinde bile lexical
# zorunlu eşleşme (bool.must) asla değişmez: kalite sinyalleri yalnızca zaten
# eşleşmiş belgelerin _score'unu function_score ile çarpar, tek başına belge
# döndürmez. script_score KULLANILMAZ — yalnızca field_value_factor (boost) ve
# filter+weight (eşik altı penalty) fonksiyonları; performans için tercih
# edilir (bkz. elasticsearch/product_quality_production_migration.md).
def _build_quality_functions(qr, bypass_must_not: list[dict] | None) -> list[dict]:
    """quality_ranking config'inden function_score `functions` listesini üretir.

    `missing_value_behavior`:
      - "neutral": alan mevcut olmayan (eski) belgeler için field_value_factor'a
        `missing = 1/factor` verilir → çarpan tam olarak 1.0 olur (ne boost ne
        penalty). Tüm belgelere aynı fonksiyon uygulanır.
      - "skip": fonksiyon yalnızca alan `exists` olduğunda çalışır; alan yoksa
        fonksiyon hiç değerlendirilmez (score_mode=multiply altında etkisi
        yine nötr 1.0'dır) — matematiksel sonuç aynıdır, farkla alanın
        varlığı ES `exists` filtresiyle açıkça kontrol edilir.

    `bypass_must_not` doluysa (exact ASIN sorgusu + bypass_for_exact_asin),
    tüm fonksiyonlara aynı "bu belge exact-ASIN eşleşmesiyse uygulama"
    filtresi eklenir.
    """

    def _wrap_filter(extra_filter: dict | None) -> dict | None:
        if not bypass_must_not:
            return extra_filter
        bool_body: dict = {"must_not": bypass_must_not}
        if extra_filter is not None:
            bool_body["must"] = [extra_filter]
        return {"bool": bool_body}

    functions: list[dict] = []

    for field, factor in ((qr.score_field, qr.boost), (qr.consistency_field, qr.consistency_boost)):
        function: dict = {"field_value_factor": {"field": field, "factor": factor, "modifier": "none"}}
        if qr.missing_value_behavior == "skip":
            filt = _wrap_filter({"exists": {"field": field}})
        else:
            function["field_value_factor"]["missing"] = round(1.0 / factor, 6)
            filt = _wrap_filter(None)
        if filt is not None:
            function["filter"] = filt
        functions.append(function)

    penalty_filter = _wrap_filter({
        "bool": {
            "must": [
                {"exists": {"field": qr.consistency_field}},
                {"range": {qr.consistency_field: {"lt": qr.low_consistency_threshold}}},
            ]
        }
    })
    functions.append({"filter": penalty_filter, "weight": qr.low_consistency_penalty})

    return functions


def _apply_quality_ranking(base_query: dict, *, query_text: str, enable_exact_asin: bool) -> dict:
    """`base_query` (`{"bool": ...}`) üzerine, açıksa, kalite sinyali
    function_score'unu bindirir. `enable_exact_asin` VE
    `quality_ranking.bypass_for_exact_asin` ikisi de doğruysa, tam bu sorgu
    metniyle eşleşen exact ASIN belgeleri kalite boost/penalty'sinden muaf
    tutulur — kullanıcı doğrudan ürün kodu aradığında kayıt her zaman
    bulunabilir olmalı, kalite sinyali bunu bastıramaz."""
    qr = CONFIG.quality_ranking
    if not qr.enabled:
        return base_query

    bypass_must_not = None
    if qr.bypass_for_exact_asin and enable_exact_asin:
        bypass_must_not = [{
            "term": {
                CONFIG.search_methods.exact_asin.field: {
                    "value": query_text,
                    "case_insensitive": True,
                }
            }
        }]

    functions = _build_quality_functions(qr, bypass_must_not)

    return {
        "function_score": {
            "query": base_query,
            "functions": functions,
            "score_mode": "multiply",
            "boost_mode": "multiply",
        }
    }


def _apply_popularity_ranking(base_query: dict, cfg: "AppConfig") -> dict:
    """`rating_number`e dayalı çarpımsal bir popülerlik/güven sinyali bindirir
    (bkz. `PopularityRankingConfig` docstring'i — çarpan = `baseline +
    ln(1 + factor*rating_number)`). İki fonksiyon `score_mode: sum` ile
    toplanıp `boost_mode: multiply` ile orijinal lexical skorla çarpılır:
    `weight: baseline` HER belgede (filtre yok) koşulsuz uygulanır, bu
    yüzden `rating_number=0`/alan eksik (`missing: 0`) olsa bile toplam
    çarpan asla `baseline`in altına inmez — `field_value_factor` tek
    başına kullanılsaydı `log1p(0)=0` çarpanı bu belgelerin skorunu
    tamamen sıfırlardı (bkz. `_build_quality_functions`'taki aynı
    'missing_value_behavior: neutral' deseni)."""
    pr = cfg.popularity_ranking
    if not pr.enabled:
        return base_query
    return {
        "function_score": {
            "query": base_query,
            "functions": [
                {"weight": pr.baseline},
                {
                    "field_value_factor": {
                        "field": pr.field,
                        "factor": pr.factor,
                        "modifier": "log1p",
                        "missing": 0,
                    }
                },
            ],
            "score_mode": "sum",
            "boost_mode": "multiply",
        }
    }


# ---------------------------------------------------------------------------
# Sorgu oluşturma (normal arama)
# ---------------------------------------------------------------------------
def _normalize_page(page: int) -> int:
    """`page < 1` girişini 1'e normalize eder (validation error fırlatmak
    yerine — diğer opsiyonel sistemlerle tutarlı 'fail safely' yaklaşımı)."""
    return page if page >= 1 else 1


def _canonical_field_name(field: str) -> str:
    """`.tr` sonekini çıkarır (`title.tr` -> `title`) — dil çiftini TEK bir
    kanonik alan olarak ele almak için (bkz. spec §4: title/title.tr aynı
    consensus/contradiction alanı olarak sayılmalı, iki ayrı alan değil)."""
    return field[: -len(".tr")] if field.endswith(".tr") else field


def _build_field_evidence_clauses(
    query_text: str, cfg: "AppConfig", *, name_prefix: str | None = None
) -> dict[str, list[dict]]:
    """`field_relevance.fields`teki her alan için TEK bir `match`
    (operator: field_relevance.operator) maddesi üretir; sonucu KANONİK
    alan adına göre gruplar. Bu sözlük hem lexical `bool.should` grubunda
    (bu fonksiyonun çağrıldığı yer) HEM DE field consensus'un (Task 3)
    filter'larında AYNEN yeniden kullanılır — consensus asla `exists` ile
    değil, burada üretilen GERÇEK `match` kanıtıyla sayılır (bkz. spec §4
    düzeltmesi).

    `name_prefix` yalnızca `include_relevance_debug=True` iken verilir;
    her maddeye `_name: f"{name_prefix}:{canonical_field}"` eklenir — ES'in
    native `matched_queries` mekanizması hangi kanonik alanın GERÇEKTEN
    eşleştiğini debug response'una taşır (bkz. spec §8, canlı cluster'da
    doğrulandı)."""
    grouped: dict[str, list[dict]] = {}
    for entry in cfg.field_relevance.fields:
        canonical = _canonical_field_name(entry.field)
        field_params: dict = {
            "query": query_text,
            "operator": cfg.field_relevance.operator,
            "boost": entry.boost,
        }
        if name_prefix:
            field_params["_name"] = f"{name_prefix}:{canonical}"
        grouped.setdefault(canonical, []).append({"match": {entry.field: field_params}})
    return grouped


def _build_cross_fields_clause(
    query_text: str, cfg: "AppConfig", *, include_relevance_debug: bool = False
) -> dict:
    """`field_relevance.fields`in tamamını TEK bir alanmış gibi ele alan
    `cross_fields` `multi_match` — sorgu terimleri farklı alanlara
    dağılmış olsa bile (`"wireless gaming mouse"` -> title="Gaming Mouse"
    + features="Wireless") zorunlu lexical kapıyı hâlâ karşılayabilir
    (bkz. spec §3, istek §5 "cross-field query"). `operator: and` olduğu
    için gevşek bag-of-words eşleşmesi DEĞİLDİR — tüm terimler hâlâ
    ZORUNLUDUR, yalnızca tek bir alanda olmak zorunda değildir."""
    clause: dict = {
        "multi_match": {
            "query": query_text,
            "type": "cross_fields",
            "operator": cfg.field_relevance.operator,
            "boost": cfg.field_relevance.cross_fields_boost,
            "fields": cfg.field_relevance.es_fields,
        }
    }
    if include_relevance_debug:
        clause["multi_match"]["_name"] = "field:cross_fields"
    return clause


_POSITIVE_CATEGORY_TEXT_FIELDS = ("categories_text", "categories_text.tr")


def _positive_category_should_clauses(positive_categories: tuple[dict, ...]) -> list[dict]:
    """`IntentSignals.positive_categories` içindeki her girdiyi bool.should
    (rerank-only) maddesine çevirir. `manual` kaynaklı girdiler serbest
    metin olabileceğinden her iki metin alanında (`categories_text` +
    `.tr`) `match_phrase` ile aranır; `dynamic` kaynaklı girdiler zaten
    tek, yetkili bir aggregation alanına (`field`) bağlı olduğundan yalnızca
    o keyword alanında `term` ile aranır.

    `manual` girdiler opsiyonel bir `boost_tr` taşıyabilir: legacy
    `category_boost_terms` (bkz. intent_service.resolve_intent_signals)
    `categories_text` için `boost`, `categories_text.tr` için AYRI
    `boost_tr` üretir — pre-branch davranışı (`watch` kuralı için 12/8)
    aynen korunur. `boost_tr` yoksa (yeni obje-biçimli `positive_categories`
    şeması, örn. `iphone_case`'in `Cases` girdisi) iki alan da AYNI `boost`
    değerini kullanır — bkz. tasarım §3 ("applied to both fields, no
    separate _tr key needed")."""
    clauses: list[dict] = []
    for entry in positive_categories:
        if entry["source"] == "manual":
            field_boosts = {
                "categories_text": entry["boost"],
                "categories_text.tr": entry.get("boost_tr", entry["boost"]),
            }
            for text_field in _POSITIVE_CATEGORY_TEXT_FIELDS:
                clauses.append({
                    "match_phrase": {text_field: {"query": entry["value"], "boost": field_boosts[text_field]}}
                })
        else:  # "dynamic" — single, authoritative field from the aggregation bucket
            clauses.append({
                "term": {entry["field"]: {"value": entry["value"], "boost": entry["boost"]}}
            })
    return clauses


def _store_boost_clause(query_text: str, cfg: "AppConfig", *, debug_names: bool = False) -> dict | None:
    """`store` (marka vekili) için düşük ağırlıklı, tek başına YETERSİZ
    yardımcı sinyal (bkz. spec §3/§6). `store` `keyword` tipinde olduğu
    için bu `match` yalnızca SORGU METNİNİN TAMAMI mağaza değerine eşit
    olduğunda ateşler (ör. query="sony", store="Sony") — bilinçli olarak
    dar bir sinyal. `field_relevance.enabled=False` veya
    `store_boost<=0` iken tamamen atlanır; çağıran taraf (`build_search_query`)
    ayrıca bu fonksiyonu yalnızca `enable_multi_match=True` iken çağırır —
    `field_relevance`'tan türetilen diğer her madde (alan-kanıtı eşleşmeleri,
    `cross_fields` fallback) aynı toggle'a bağlı olduğundan bu tutarlılığı
    korur (final review bulgusu 6). Consensus/contradiction sayımlarına
    ASLA dahil edilmez (bkz. _apply_field_consensus/
    _apply_relevance_contradiction — ikisi de bu alana hiç değinmez)."""
    fr = cfg.field_relevance
    if not fr.enabled or fr.store_boost <= 0:
        return None
    field_params: dict = {"query": query_text, "boost": fr.store_boost}
    if debug_names:
        field_params["_name"] = "field:store"
    return {"match": {"store": field_params}}


def _soft_negative_boosting_negative_clause(negative_categories: tuple[dict, ...]) -> tuple[dict | None, float]:
    """Soft negatif kategori girdilerinden `boosting.negative` bloğunu ve
    tek skaler `negative_boost` değerini üretir (en güçlü — en düşük —
    penalty). Girdi boşsa (None, 1.0) döner (no-op)."""
    if not negative_categories:
        return None, 1.0
    should = []
    for entry in negative_categories:
        value = entry["value"]
        should.extend([
            {"term": {"main_category": value}},
            {"term": {"source_category": value}},
            {"term": {"categories": value}},
            {"match_phrase": {"categories_text": {"query": value}}},
            {"match_phrase": {"categories_text.tr": {"query": value}}},
        ])
    negative_boost = min(entry["penalty"] for entry in negative_categories)
    return {"bool": {"should": should, "minimum_should_match": 1}}, negative_boost


def _consensus_tier_filter(
    evidence_by_field: dict[str, list[dict]], counted_fields: tuple[str, ...], minimum: int, *, name: str | None = None
) -> dict:
    """`counted_fields`teki her kanonik alan için, o alanın GERÇEK kanıt
    maddelerinden (Task 2'nin ürettiği aynı `match` nesneleri — YENİDEN
    TÜRETİLMEZ) tek bir OR grubu kurar, sonra bu grupları
    `minimum_should_match: minimum` ile sayar. Bir alanın hiç kanıt
    maddesi yoksa (`field_relevance.fields`te tanımlı değilse) o alan
    sessizce atlanır — asla `exists` kullanılmaz (bkz. spec §4 düzeltmesi)."""
    should: list[dict] = []
    for field in counted_fields:
        clauses = evidence_by_field.get(field)
        if not clauses:
            continue
        should.append({"bool": {"should": clauses, "minimum_should_match": 1}})
    filt: dict = {"bool": {"should": should, "minimum_should_match": minimum}}
    if name:
        filt["bool"]["_name"] = name
    return filt


def _apply_field_consensus(
    base_query: dict, evidence_by_field: dict[str, list[dict]], cfg: "AppConfig", *, debug_names: bool = False
) -> dict:
    """Bağımsız birden fazla alanda GERÇEKTEN eşleşen belgeler için
    kademeli çarpan uygular (bkz. spec §4). `score_mode: max` yalnızca EN
    YÜKSEK uygulanabilir kademenin ağırlığını kullanır (bileşik/katlanmış
    ÇARPIM DEĞİL); hiçbir kademe eşleşmezse fonksiyon no-op'tur
    (`boost_mode: multiply` × hiçbir şey = ×1) — `_build_quality_functions`
    ile aynı filter+weight kuralı, `script_score` YOK."""
    fc = cfg.field_consensus
    if not fc.enabled or not evidence_by_field:
        # evidence_by_field boş ise (enable_multi_match=False veya
        # field_relevance.enabled=False) sarmalayici tamamen atlanir --
        # aksi halde _consensus_tier_filter her kademe icin BOS bir
        # `should` listesiyle bir filtre uretirdi; Elasticsearch bos
        # bool.should'u minimum_should_match uygulanmadan ONCE match_all'a
        # cevirir, yani kanit sifir olsa bile HER belge en ust kademe
        # (four_plus) bonusunu alirdi -- "kanitsiz belge asla bonus almaz"
        # degismezinin dogrudan ihlali (final review bulgusu 1).
        return base_query
    tiers = (
        (2, fc.two_field_boost, "consensus:two_plus"),
        (3, fc.three_field_boost, "consensus:three_plus"),
        (4, fc.four_plus_field_boost, "consensus:four_plus"),
    )
    functions = [
        {
            "filter": _consensus_tier_filter(
                evidence_by_field, fc.counted_fields, minimum, name=name if debug_names else None
            ),
            "weight": weight,
        }
        for minimum, weight, name in tiers
    ]
    return {
        "function_score": {
            "query": base_query,
            "functions": functions,
            "score_mode": "max",
            "boost_mode": "multiply",
        }
    }


def _contradiction_field_clause(field: str, terms: tuple[str, ...], *, debug_names: bool = False) -> dict:
    """`terms`den herhangi birinin `field`de GERÇEKTEN `match_phrase` ile
    eşleştiği bir OR grubu kurar — yine `exists` DEĞİL (bkz. spec §5
    düzeltmesi); bir alan yalnızca kuralın kendi `contradiction_terms`inden
    biri o alanda fiilen geçtiğinde 'çelişiyor' sayılır."""
    should = [{"match_phrase": {field: term}} for term in terms]
    clause: dict = {"bool": {"should": should, "minimum_should_match": 1}}
    if debug_names:
        clause["bool"]["_name"] = f"contradiction:{field}"
    return clause


def _apply_relevance_contradiction(
    base_query: dict, contradiction_terms: tuple[str, ...], cfg: "AppConfig", *, debug_names: bool = False
) -> dict:
    """Bağımsız birden fazla alanda (title HARİÇ — bkz. spec §5) GERÇEKTEN
    çelişen belgeler için kademeli PENALTY uygular; asla `must_not` üretmez,
    yalnızca `boost_mode: multiply` ile skoru küçültür. `score_mode: min`
    KRİTİKTİR — adaylar arasından EN GÜÇLÜ (en düşük) uygulanabilir
    penaltının seçilmesini sağlar; `max` kullanılsaydı her zaman 1.0 (no-op)
    seçilir ve özellik sessizce devre dışı kalırdı.

    Her kademenin filtresi kendi adını taşır (`contradiction_tier:mild`/
    `contradiction_tier:strong`, yalnızca `debug_names=True` iken) — bu,
    hangi kademenin ateşlendiğini `matched_queries`den DOĞRUDAN okumayı
    sağlar (bkz. Task 7); alan-sayısı eşiklerini İKİNCİ bir Python
    fonksiyonunda YENİDEN KODLAMAYA gerek kalmaz — tek doğruluk kaynağı
    burasıdır."""
    rc = cfg.relevance_contradiction
    if not rc.enabled or not contradiction_terms:
        return base_query

    per_field_clauses = [
        _contradiction_field_clause(field, contradiction_terms, debug_names=debug_names)
        for field in rc.counted_fields
    ]
    tiers = (
        (rc.minimum_conflicting_fields, rc.mild_penalty, "contradiction_tier:mild"),
        (rc.strong_conflicting_fields, rc.strong_penalty, "contradiction_tier:strong"),
    )
    functions = []
    for minimum, weight, name in tiers:
        filt: dict = {"bool": {"should": per_field_clauses, "minimum_should_match": minimum}}
        if debug_names:
            filt["bool"]["_name"] = name
        functions.append({"filter": filt, "weight": weight})

    return {
        "function_score": {
            "query": base_query,
            "functions": functions,
            "score_mode": "min",
            "boost_mode": "multiply",
        }
    }


def _apply_dynamic_category_penalty(
    base_query: dict, discovered_categories: list[dict], cfg: "AppConfig"
) -> dict:
    """`discover_category_intent`'in bu sorgu için bulduğu TOP `main_category`
    adaylarının (baskın kategoriler) DIŞINDA kalan belgelere hafif bir
    çarpımsal penaltı uygular. Amaç: lexical olarak eşleşen ama kategorisi
    sorguyla tamamen alakasız ürünlerin (ör. "wireless headphones" aramasında
    Automotive/Arts&Crafts kategorili, tek kelimesi rastgele eşleşen ürünler
    — bkz. CLAUDE.md relevance notları) BM25'in kısa-alan/nadir-terim
    şişirmesinden aldığı avantajı dengelemek.

    `discovered_categories`, `resolve_intent_signals`'ın zaten hesapladığı
    (`IntentSignals.debug["dynamic_discovery"]`) TEK aggregation isteğinin
    çıktısıdır — bunun için EKSTRA bir Elasticsearch isteği YAPILMAZ.

    `main_category` `aggregation_fields`te değilse ya da keşif hiç aday
    üretmemişse (fail-safe: kapalı, min. sorgu uzunluğu altı, timeout,
    bağlantı hatası) `top_main_categories` boş kalır ve bu fonksiyon
    no-op'tur — kategori keşfi ASLA normal aramayı tek başına bozamaz
    (bkz. CLAUDE.md §16). `weight: 1.0` filtresiz fonksiyon, penaltı
    filtresi eşleşmeyen (yani TOP kategoride olan) belgeler için çarpanın
    tam olarak 1.0 (nötr) kalmasını garanti eder — `_build_quality_functions`
    ile aynı 'filtresiz taban + filtreli penaltı, score_mode: multiply'
    deseni."""
    dyn = cfg.dynamic_intent
    if not dyn.enabled or dyn.negative_category_penalty >= 1.0:
        return base_query

    top_main_categories = sorted({
        str(candidate["value"])
        for candidate in discovered_categories
        if candidate.get("field") == "main_category" and candidate.get("value")
    })
    if not top_main_categories:
        return base_query

    penalty_filter = {"bool": {"must_not": [{"terms": {"main_category": top_main_categories}}]}}
    return {
        "function_score": {
            "query": base_query,
            "functions": [
                {"weight": 1.0},
                {"filter": penalty_filter, "weight": dyn.negative_category_penalty},
            ],
            "score_mode": "multiply",
            "boost_mode": "multiply",
        }
    }


def relevance_debug_from_matched_queries(matched_queries: list[str] | None, cfg: "AppConfig") -> dict:
    """ES'in native `_name`/`matched_queries` mekanizmasından (bkz.
    build_search_query(include_relevance_debug=True), spec §8 — canlı
    cluster'da doğrulandı: function_score `filter` içindeki `_name`'ler de
    `hit['matched_queries']`'e yansıyor) türetilmiş SAF bir özetleme
    fonksiyonu. Hiçbir alan burada YENİDEN TAHMİN edilmez — yalnızca
    sorgunun kendisinin GERÇEKTEN eşleştirdiği adlandırılmış maddeler
    okunur; `_explain` çağrısı YAPILMAZ (bkz. spec §11 performans notu —
    bu fonksiyon ekstra bir ES isteği gerektirmez, aynı `_search`
    yanıtından okunur)."""
    matched = set(matched_queries or [])
    matched_fields = [f for f in cfg.field_consensus.counted_fields if f"field:{f}" in matched]
    contradictions = [f for f in cfg.relevance_contradiction.counted_fields if f"contradiction:{f}" in matched]

    rc = cfg.relevance_contradiction
    if "contradiction_tier:strong" in matched:
        applied_penalty = rc.strong_penalty
    elif "contradiction_tier:mild" in matched:
        applied_penalty = rc.mild_penalty
    else:
        applied_penalty = 1.0

    return {
        "matched_fields": matched_fields,
        "consensus_level": len(matched_fields),
        "contradictions": contradictions,
        "applied_penalty": applied_penalty,
    }


def build_search_query(
    query_text: str,
    enable_phrase: bool = True,
    enable_multi_match: bool = True,
    enable_fuzzy: bool = True,
    enable_exact_asin: bool = True,
    result_size: int | None = None,
    page: int = 1,
    page_size: int | None = None,
    track_total_hits: bool | None = None,
    apply_intent_reranking: bool = True,
    intent_signals: "IntentSignals | None" = None,
    *,
    config: "AppConfig | None" = None,
    include_relevance_debug: bool = False,
) -> dict:
    """
    Seçili yöntemlere göre intent-farkındalıklı bir Elasticsearch sorgusu üretir.
    Alan adları ve boostlar config/search_config.json'daki search_methods'tan
    okunur.

    SAF (I/O yapmaz) bir fonksiyondur — Elasticsearch'e istek atmaz. Dinamik
    kategori keşfi (Elasticsearch aggregation isteği gerektirir) burada değil,
    `search_products` içinde `resolve_intent_signals` ile hesaplanır ve
    sonucu `intent_signals` (bir `IntentSignals`) olarak bu fonksiyona enjekte
    edilir. `intent_signals` `None` bırakılırsa (varsayılan çağrı biçimi),
    yalnızca manuel `intent_rules.json` kuralları (`intent_service.resolve_intent_signals`,
    çeviri varyantı/dinamik aday olmadan, saf) kullanılır — böylece bu
    fonksiyon testlerde ağ çağrısı yapmadan doğrudan çağrılabilir.

    Lexical yöntemler (aç/kapa):
      A) parent_asin exact `term`   (enable_exact_asin)
      B) title `match_phrase`        (enable_phrase)
      C) full-text `multi_match`     (enable_multi_match)
      D) fuzzy `multi_match`         (enable_fuzzy)

    Yapı:
      bool.must   → [ bool.should=lexical (+ çeviri alternatifleri), minimum_should_match=1 ]
      bool.should → intent kategori boostları (manuel + dinamik pozitif kategoriler; yalnızca sıralamayı iyileştirir)
      bool.must_not → yalnızca `legacy_hard_exclusions` (ör. watch→books; kontrollü, geriye dönük uyumlu sert dışlama)
      Soft negatif kategoriler (`intent_signals.negative_categories`) ASLA
      `must_not`'a girmez — yalnızca dış `boosting.negative` sarmalayıcısı
      içinde skor düşürücü olarak kullanılır (bkz. `_soft_negative_boosting_negative_clause`);
      bu nedenle tek başlarına hiçbir belgeyi elemezler.

    Böylece kategori boostları tek başına belge döndürmez; ürün önce lexical
    (veya çevrilmiş bir lexical alternatif) olarak eşleşmek zorundadır.
    Hiç lexical yöntem yoksa `match_none` döner.

    Sayfalama (`from + size`):
      `pagination.enabled` iken `page`/`page_size` (page_size verilmezse
      `pagination.page_size`) `from`/`size` değerlerini belirler ve
      `result_size` parametresi YOK SAYILIR — iki alan asla çelişmez, tek
      bir öncelik kuralı vardır (bkz. config.PaginationConfig). Devre dışıyken
      davranış değişmez: `result_size` (veya `limits.result_size`) tek
      başına `size`'ı belirler, `from` payload'a hiç eklenmez.
      `from + size`, `pagination.max_result_window`'ı aşarsa
      `PaginationLimitError` fırlatılır — sorgu hiç oluşturulmaz.
    """
    cfg = config or CONFIG
    methods = cfg.search_methods
    pagination = cfg.pagination
    track_total_hits = (
        track_total_hits if track_total_hits is not None else cfg.elasticsearch.track_total_hits
    )

    normalized_page = _normalize_page(page)
    if pagination.enabled:
        size = page_size if page_size is not None else pagination.page_size
        from_ = (normalized_page - 1) * size
        if from_ + size > pagination.max_result_window:
            raise PaginationLimitError(normalized_page, pagination.max_allowed_page)
    else:
        size = result_size if result_size is not None else cfg.limits.result_size
        from_ = None

    lexical_queries = []

    # A. Exact ürün kodu eşleşmesi (keyword alan → term)
    if enable_exact_asin:
        lexical_queries.append({
            "term": {
                methods.exact_asin.field: {
                    "value": query_text,
                    "boost": methods.exact_asin.boost,
                    "case_insensitive": True,
                }
            }
        })

    # B. Tam ifadeye yakın başlık eşleşmesi
    if enable_phrase:
        lexical_queries.append({
            "match_phrase": {
                methods.phrase.field: {
                    "query": query_text,
                    "boost": methods.phrase.boost,
                }
            }
        })

    # C. Field relevance — sorgu kanıtını title/features/description/
    # categories_text arasında BAĞIMSIZ olarak toplar (eski dis_max
    # multi_match'in YERİNE geçer — additif puanlama field consensus'u
    # (bkz. build_search_query çağrısındaki §D-consensus wrapper) mümkün
    # kılar). `field_relevance_evidence` Task 3/5'te consensus/contradiction
    # filter'larında yeniden kullanılmak üzere fonksiyon gövdesinde saklanır.
    field_relevance_evidence: dict[str, list[dict]] = {}
    if enable_multi_match and cfg.field_relevance.enabled:
        name_prefix = "field" if include_relevance_debug else None
        field_relevance_evidence = _build_field_evidence_clauses(query_text, cfg, name_prefix=name_prefix)
        for clauses in field_relevance_evidence.values():
            lexical_queries.extend(clauses)
        lexical_queries.append(
            _build_cross_fields_clause(query_text, cfg, include_relevance_debug=include_relevance_debug)
        )

    # D. Fuzzy multi_match (yazım hataları)
    if enable_fuzzy:
        lexical_queries.append({
            "multi_match": {
                "query": query_text,
                "type": methods.fuzzy.type,
                "operator": methods.fuzzy.operator,
                "fuzziness": methods.fuzzy.fuzziness,
                "prefix_length": methods.fuzzy.prefix_length,
                "max_expansions": methods.fuzzy.max_expansions,
                "boost": methods.fuzzy.boost,
                "fields": methods.fuzzy.es_fields,
            }
        })

    # E/F. Title katmanlı skorlama — yalnızca title_ranking.enabled ile
    # kontrol edilir (enable_phrase'e BAĞLI DEĞİLDİR; phrase search kapalı
    # olsa bile bu iki katman çalışabilir — bkz. spec §5 düzeltmesi).
    title_ranking = cfg.title_ranking
    if title_ranking.enabled:
        lexical_queries.append({
            "term": {
                title_ranking.exact_field: {
                    "value": query_text,
                    "boost": title_ranking.exact_boost,
                    "case_insensitive": True,
                }
            }
        })
        lexical_queries.append({
            "match_phrase_prefix": {
                "title": {
                    "query": query_text,
                    "boost": title_ranking.prefix_boost,
                    "max_expansions": title_ranking.prefix_max_expansions,
                }
            }
        })

    # Hiç lexical yöntem yoksa güvenlik ağı.
    if not lexical_queries:
        payload = {
            "size": size,
            "track_total_hits": track_total_hits,
            "_source": list(cfg.source_fields.search),
            "query": {"match_none": {}},
        }
        if from_ is not None:
            payload["from"] = from_
        return payload

    # Çeviri sözlüğünden gelen alternatifler zorunlu eşleşme grubuna eklenir
    # (bypass etmez — ek bir "veya" seçeneğidir).
    lexical_queries.extend(_build_translation_lexical_queries(query_text))

    # Intent sinyalleri. Çağıran taraf (search_products) zaten
    # resolve_intent_signals ile manuel+dinamik sinyalleri hesaplayıp enjekte
    # ettiyse o `IntentSignals` kullanılır; aksi halde (varsayılan, saf çağrı)
    # yalnızca manuel kurallar hesaplanır — lexical eşleşme zorunluluğunu
    # değiştirmez.
    if apply_intent_reranking:
        if intent_signals is None:
            intent_signals = intent_service.resolve_intent_signals(
                query_text, [], INTENT_RULES, [], config=cfg
            )
    else:
        intent_signals = IntentSignals()

    bool_query = {
        "must": [
            {
                "bool": {
                    "should": lexical_queries,
                    "minimum_should_match": 1,
                }
            }
        ],
    }
    should_clauses = _positive_category_should_clauses(intent_signals.positive_categories)
    # `field_relevance`'tan turetilen diger her madde (alan-kaniti eslesmeleri,
    # cross_fields fallback) enable_multi_match ile kapatiliyor -- store-boost
    # yardimci maddesi de aynı sekilde bu toggle'a bagli olmalidir, aksi halde
    # "Cok alanli arama" kapatildiginda tutarsiz bicimde acik kalirdi (final
    # review bulgusu 6).
    if enable_multi_match:
        store_clause = _store_boost_clause(query_text, cfg, debug_names=include_relevance_debug)
        if store_clause is not None:
            should_clauses.append(store_clause)
    if should_clauses:
        bool_query["should"] = should_clauses
    if intent_signals.legacy_hard_exclusions:
        bool_query["must_not"] = list(intent_signals.legacy_hard_exclusions)

    base_query = {"bool": bool_query}

    base_query = _apply_field_consensus(
        base_query, field_relevance_evidence, cfg, debug_names=include_relevance_debug
    )

    base_query = _apply_relevance_contradiction(
        base_query, intent_signals.contradiction_terms, cfg, debug_names=include_relevance_debug
    )

    base_query = _apply_dynamic_category_penalty(
        base_query, list(intent_signals.debug.get("dynamic_discovery", [])), cfg
    )

    negative_clause, negative_boost = _soft_negative_boosting_negative_clause(
        intent_signals.negative_categories
    )
    if negative_clause is not None:
        base_query = {
            "boosting": {
                "positive": base_query,
                "negative": negative_clause,
                "negative_boost": negative_boost,
            }
        }

    final_query = _apply_quality_ranking(
        base_query, query_text=query_text, enable_exact_asin=enable_exact_asin
    )
    final_query = _apply_popularity_ranking(final_query, cfg)

    payload = {
        "size": size,
        "track_total_hits": track_total_hits,
        "_source": list(cfg.source_fields.search),
        "query": final_query,
    }
    if from_ is not None:
        payload["from"] = from_
    return payload


# ---------------------------------------------------------------------------
# Elasticsearch isteği
# ---------------------------------------------------------------------------
def _post_search(payload: dict, timeout: int = 20, index: str = None, search_type: str | None = None):
    """
    Verilen sorgu gövdesini belirtilen index üzerinde Elastic Cloud'a gönderir;
    ortak HTTP ve hata yönetimini tek yerde toplar. Varsayılan index normal
    aramanın kullandığı INDEX_NAME'dir.

    `autocomplete_service` bu fonksiyonu modül referansıyla
    (`search_service._post_search(...)`) çağırır — bkz. modül docstring'i.

    `search_type`: verilirse `?search_type=...` query string parametresi
    olarak eklenir (ör. `dfs_query_then_fetch` — bkz. `search_products`'taki
    kullanım ve `ElasticsearchConfig.use_dfs_query_then_fetch` yorumu).
    Varsayılan `None` iken ES'in kendi varsayılanı (`query_then_fetch`)
    kullanılır — aggregation-only (kategori keşfi) ve autocomplete çağrıları
    bunu hiç geçirmez, davranışları değişmez.

    Dönüş: (data_dict, hata_mesaji). Hata varsa data_dict None döner.
    """
    if index is None:
        index = INDEX_NAME

    headers = {
        "Authorization": f"ApiKey {ES_API_KEY}",
        "Content-Type": "application/json",
    }
    params = {"search_type": search_type} if search_type else None

    try:
        response = requests.post(
            f"{ES_URL}/{index}/_search",
            headers=headers,
            json=payload,
            timeout=timeout,
            params=params,
        )
    except requests.exceptions.ConnectionError:
        return None, CONFIG.ui.message("connection_error")
    except requests.exceptions.Timeout:
        return None, CONFIG.ui.message("timeout_error")
    except requests.exceptions.RequestException:
        return None, CONFIG.ui.message("generic_request_error")

    if response.status_code == 401:
        return None, CONFIG.ui.message("auth_error")
    if response.status_code == 403:
        return None, CONFIG.ui.message("forbidden_error", index=index)
    if response.status_code == 404:
        return None, CONFIG.ui.message("not_found_error", index=index)
    if response.status_code >= 400:
        # Elasticsearch hata gövdesini güvenli biçimde mesaja ekle.
        detail = ""
        try:
            body = response.json()
            reason = (
                body.get("error", {}).get("reason")
                if isinstance(body.get("error"), dict)
                else body.get("error")
            )
            detail = f" — {reason}" if reason else f" — {str(body)[:300]}"
        except ValueError:
            detail = f" — {response.text[:300]}" if response.text else ""
        return None, CONFIG.ui.message("es_error", status=response.status_code, detail=detail)

    try:
        return response.json(), None
    except ValueError:
        return None, CONFIG.ui.message("response_decode_error")


def search_products(
    query_text: str,
    enable_phrase: bool = True,
    enable_multi_match: bool = True,
    enable_fuzzy: bool = True,
    enable_exact_asin: bool = True,
    page: int = 1,
    *,
    fetch_aggregations=None,
    config: "AppConfig | None" = None,
    include_relevance_debug: bool = False,
) -> SearchResult:
    """
    Seçili yöntemlerle üretilen gelişmiş sorguyu Elastic Cloud'a gönderir.

    Normal arama akışının GERÇEK giriş noktasıdır: burada önce
    `resolve_intent_signals` çağrılır (manuel `intent_rules.json` kuralları +
    dinamik kategori keşfi Elasticsearch aggregation isteğini içerir), sonra
    sonuç saf `build_search_query`'ye enjekte edilir. Kategori keşfi
    başarısız olursa (bkz. discover_category_intent) sessizce boş liste
    döner; bu, ana ürün aramasını asla engellemez.

    `fetch_aggregations`: kategori keşfi için kullanılacak fetcher (bkz.
    `discover_category_intent`); `app.py` burada kendi `st.cache_data`
    sarmalayıcısını enjekte eder.
    `config`: opsiyonel `AppConfig` override'ı (bkz. `build_search_query`);
    `resolve_intent_signals` ve `build_search_query` çağrılarına aktarılır.

    Sayfalama: `page` (1-tabanlı), `pagination.enabled` iken
    `build_search_query`'ye aktarılır. `from + size`,
    `pagination.max_result_window`'ı aşarsa (`PaginationLimitError`)
    Elasticsearch'e hiç istek atılmaz; kullanıcıya anlaşılır bir mesajla
    (`pagination_limit_error`) `SearchResult(error=...)` döner — bu bir
    çökme değil, kontrollü bir sınırdır.

    Dönüş: `SearchResult`. Hata varsa `hits` None döner.
    """
    cfg = config or CONFIG
    pagination = cfg.pagination
    normalized_page = _normalize_page(page)
    page_size = pagination.page_size if pagination.enabled else cfg.limits.result_size

    intent_signals = resolve_intent_signals(
        query_text, include_dynamic=True, fetch_aggregations=fetch_aggregations, config=cfg
    )
    try:
        payload = build_search_query(
            query_text,
            enable_phrase=enable_phrase,
            enable_multi_match=enable_multi_match,
            enable_fuzzy=enable_fuzzy,
            enable_exact_asin=enable_exact_asin,
            result_size=cfg.limits.result_size,
            page=normalized_page,
            track_total_hits=True,
            intent_signals=intent_signals,
            config=cfg,
            include_relevance_debug=include_relevance_debug,
        )
    except PaginationLimitError as limit_error:
        message = CONFIG.ui.message(
            "pagination_limit_error",
            max_result_window=pagination.max_result_window,
            max_page=limit_error.max_allowed_page,
        )
        return SearchResult(
            hits=None,
            total=0,
            error=message,
            current_page=normalized_page,
            page_size=page_size,
            total_pages=limit_error.max_allowed_page,
            start_item=0,
            end_item=0,
            has_previous=normalized_page > 1,
            has_next=False,
        )

    data, error = _post_search(
        payload,
        timeout=cfg.elasticsearch.search_timeout_seconds,
        search_type="dfs_query_then_fetch" if cfg.elasticsearch.use_dfs_query_then_fetch else None,
    )
    if error:
        return SearchResult(
            hits=None,
            total=0,
            error=error,
            current_page=normalized_page,
            page_size=page_size,
            total_pages=0,
            start_item=0,
            end_item=0,
            has_previous=normalized_page > 1,
            has_next=False,
        )

    hits = data.get("hits", {}).get("hits", [])
    total = data.get("hits", {}).get("total", {}).get("value", len(hits))

    total_pages = (total + page_size - 1) // page_size if total > 0 else 0
    if pagination.enabled and total_pages:
        total_pages = min(total_pages, pagination.max_allowed_page)
    start_item = (normalized_page - 1) * page_size + 1 if hits else 0
    end_item = start_item + len(hits) - 1 if hits else 0

    return SearchResult(
        hits=hits,
        total=total,
        error=None,
        current_page=normalized_page,
        page_size=page_size,
        total_pages=total_pages,
        start_item=start_item,
        end_item=end_item,
        has_previous=normalized_page > 1,
        has_next=normalized_page < total_pages,
    )
