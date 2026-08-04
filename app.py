"""
Ürün Arama — Elastic Cloud destekli, tıklanabilir kartlı e-ticaret arama arayüzü.

Çalıştırma:
    pip install -r requirements.txt

    # Linux / macOS:
    #   export ELASTICSEARCH_URL="https://<deployment>.es.<region>.cloud.es.io"
    #   export ELASTICSEARCH_API_KEY="<api_key>"
    # Windows (PowerShell):
    #   $env:ELASTICSEARCH_URL="https://<deployment>.es.<region>.cloud.es.io"
    #   $env:ELASTICSEARCH_API_KEY="<api_key>"

    streamlit run app.py

Arama davranışı (index adları, boostlar, limitler, UI metinleri, intent
kuralları, çeviri sözlüğü) `config/` altındaki JSON dosyalarından ve
`config.py`'den okunur. Sırlar (URL/API key) yalnızca ortam değişkenlerinden
gelir; hiçbir zaman config dosyalarına yazılmaz.
"""

import html
import os
import re

import requests
import streamlit as st
import streamlit.components.v1 as components

from config import ConfigError, load_intent_rules, load_search_config, load_translations

# streamlit-keyup: gerçek tuş-bazlı (debounce'lı) canlı input. Kurulu değilse
# uygulama düz st.text_input'a düşer (bozulmaz), ancak canlılık azalır.
try:
    from st_keyup import st_keyup
    HAS_KEYUP = True
except Exception:  # ImportError ve olası kurulum sorunları
    HAS_KEYUP = False

# ---------------------------------------------------------------------------
# Yapılandırma yükleme
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

# Yalnızca ana indexlerde arama yapılır (test ve v1 dahil edilmez).
INDEX_NAME = CONFIG.elasticsearch.search_index_expr if CONFIG else ""
# Canlı öneriler ayrı bir Edge NGram autocomplete indexinden gelir.
AUTOCOMPLETE_INDEX_NAME = CONFIG.elasticsearch.autocomplete_index_expr if CONFIG else ""
RESULT_SIZE = CONFIG.limits.result_size if CONFIG else 20

# Tam ürün aramasında ve canlı önerilerde yanıtta dönen alanlar.
SOURCE_FIELDS = list(CONFIG.source_fields.search) if CONFIG else []
SUGGESTION_SOURCE_FIELDS = list(CONFIG.source_fields.suggestions) if CONFIG else []
PLACEHOLDER_IMAGE = CONFIG.ui.placeholder_image if CONFIG else ""

# st.cache_data'nın ttl parametresi decorator uygulanırken (import zamanında)
# değerlendirilir; bu yüzden CONFIG yüklendikten hemen sonra modül seviyesinde
# sabitlere okunur. CONFIG yüklenemezse (bkz. CONFIG_ERROR) güvenli varsayılanlar
# kullanılır — check_configuration() zaten kullanıcıyı ayrı olarak uyarır.
_DISCOVERY_CACHE_TTL = CONFIG.dynamic_intent.cache_ttl_seconds if CONFIG else 300
_AUTOCOMPLETE_CACHE_TTL = CONFIG.limits.autocomplete_cache_ttl_seconds if CONFIG else 30

st.set_page_config(page_title="Ürün Arama", page_icon="🔍", layout="wide")

# ---------------------------------------------------------------------------
# Sayfa CSS — modern hero, chip'ler, grid ve boş durum
# ---------------------------------------------------------------------------
st.markdown(
    """
    <style>
        .block-container { max-width: 1180px; padding-top: 2rem; }

        /* Hero bölümü */
        .hero {
            text-align: center;
            padding: 1.2rem 0 0.4rem 0;
        }
        .hero-logo {
            font-size: 2.4rem;
            line-height: 1;
            margin-bottom: 0.4rem;
        }
        .hero-title {
            font-size: 2.1rem;
            font-weight: 800;
            letter-spacing: -0.02em;
            margin: 0 0 0.35rem 0;
        }
        .hero-subtitle {
            opacity: 0.7;
            font-size: 1rem;
            margin: 0 auto 0.4rem auto;
            max-width: 620px;
        }

        /* Aktif özellik chip'leri */
        .chip-row { display: flex; flex-wrap: wrap; gap: 8px; margin: 0.4rem 0 0.2rem 0; }
        .chip {
            display: inline-flex; align-items: center; gap: 6px;
            font-size: 0.78rem; font-weight: 600;
            padding: 4px 12px; border-radius: 999px;
            border: 1px solid var(--chip-border, #d1d5db);
            background: var(--chip-bg, #f3f4f6);
            color: var(--chip-fg, #374151);
        }
        .chip-on {
            border-color: #c7d2fe; background: #eef2ff; color: #4338ca;
        }

        /* Sonuç üst bilgisi */
        .result-header {
            margin: 1.2rem 0 0.6rem 0;
        }
        .result-header .rh-query { font-size: 1.15rem; font-weight: 700; }
        .result-header .rh-meta { font-size: 0.9rem; opacity: 0.7; margin-top: 2px; }
        .intent-badge {
            display: inline-block; margin-top: 6px;
            font-size: 0.8rem; font-weight: 600;
            padding: 3px 10px; border-radius: 999px;
            background: #ecfdf5; color: #047857; border: 1px solid #a7f3d0;
        }

        /* Öneri paneli başlığı */
        .suggest-panel-title {
            font-size: 0.95rem; font-weight: 700; margin: 0.4rem 0 0.2rem 0;
        }

        /* Boş durum */
        .empty-state {
            text-align: center;
            opacity: 0.75;
            padding: 2.5rem 0 1rem 0;
        }
        .empty-state .es-icon { font-size: 2.6rem; }
        .empty-state .es-text { font-size: 1rem; opacity: 0.7; margin-top: 0.3rem; }

        /* Örnek sorgu chip butonları (Streamlit butonlarını sadeleştir) */
        div[data-testid="stButton"] > button {
            border-radius: 10px;
        }
    </style>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# Yapılandırma kontrolü
# ---------------------------------------------------------------------------
def check_configuration():
    """Gerekli ortam değişkenlerini ve config dosyalarını kontrol eder;
    eksik/geçersizse hata mesajı döner."""
    if CONFIG_ERROR:
        if CONFIG is not None:
            return CONFIG.ui.message("config_load_error", error=CONFIG_ERROR)
        return f"Yapılandırma yüklenemedi: {CONFIG_ERROR}"

    missing = []
    if not ES_URL:
        missing.append("ELASTICSEARCH_URL")
    if not ES_API_KEY:
        missing.append("ELASTICSEARCH_API_KEY")

    if missing:
        return CONFIG.ui.message("config_missing_env", missing=", ".join(missing))
    return None


# ---------------------------------------------------------------------------
# Niyet (intent) tespiti — kategori farkındalıklı reranking
# ---------------------------------------------------------------------------
# Kurallar config/intent_rules.json'dan yüklenir (INTENT_RULES). Bu, kolay
# genişletilebilir bir yapı sağlar: yeni bir intent eklemek için kod
# değiştirmeye gerek yoktur, JSON dosyasına yeni bir kural eklemek yeterlidir.
def detect_search_intent(query_text: str) -> dict:
    """
    Sorgu metninden kategori niyetini tespit eder (casefold ile normalize).

    Dönüş:
      {"intent": <ad|None>, "apply_exclusion": <bool>, "rule": <IntentRule|None>}

    Kural: niyet terimlerinden biri geçiyorsa niyet algılanır. Ancak sorguda
    dışlama tetikleyici bir terim (ör. "book") de varsa niyet algılanır fakat
    dışlama UYGULANMAZ (ör. hem tetikleyici hem dışlama terimi aynı sorguda
    geçerse dışlama iptal edilir; kurallar tamamen config'ten gelir).
    """
    text = (query_text or "").casefold()

    for name, rule in INTENT_RULES.items():
        if not rule.enabled:
            continue

        hit = any(_contains_term(text, t) for t in rule.query_terms)
        if not hit:
            continue

        blocked = any(_contains_term(text, t) for t in rule.excluded_when_query_contains)
        return {
            "intent": name,
            "apply_exclusion": not blocked,
            "rule": rule,
        }

    return {"intent": None, "apply_exclusion": False, "rule": None}


def _contains_term(text: str, term: str) -> bool:
    """
    `term`'in `text` içinde kelime sınırıyla geçip geçmediğini kontrol eder
    (casefold edilmiş metin beklenir). Çok kelimeli terimler alt-dizi olarak aranır.
    """
    term = term.casefold()
    if " " in term:
        return term in text
    # Tek kelime: kelime sınırı ile ara (ör. bir terim, o terimi içeren daha
    # uzun bir kelimenin alt dizesi olarak yanlışlıkla eşleşmesin).
    return re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text) is not None


def _build_intent_signals(query_text: str):
    """
    Sorgudan kategori-intent boost ve dışlama sorgularını üretir (paylaşılan
    mantık; hem normal arama hem autocomplete önerileri kullanır).

    Dönüş: (intent_boost_queries, intent_exclusions)
    Bu sorgular tek başına belge döndürmez; yalnızca ana eşleşmenin üzerine
    reranking/dışlama sinyali ekler.
    """
    intent_boost_queries = []
    intent_exclusions = []
    info = detect_search_intent(query_text)
    rule = info.get("rule")
    if rule:
        for term in rule.category_boost_terms:
            intent_boost_queries.append({
                "match_phrase": {"categories_text": {"query": term, "boost": rule.category_boost}}
            })
            intent_boost_queries.append({
                "match_phrase": {"categories_text.tr": {"query": term, "boost": rule.category_boost_tr}}
            })
        if info.get("apply_exclusion"):
            # Kitap belgeleri bazen yalnızca main_category/source_category
            # üzerinden kategori taşıyor (categories/categories_text boş olabiliyor).
            # Bu yüzden dışlamayı tüm ilgili alanlara uygula: keyword alanlarda
            # term, text alanlarda match_phrase. Her terim için bir bool.should
            # (minimum_should_match=1) bloğu must_not'a eklenir.
            for term in rule.negative_categories:
                intent_exclusions.append({
                    "bool": {
                        "should": [
                            {"term": {"main_category": term}},
                            {"term": {"source_category": term}},
                            {"term": {"categories": term}},
                            {"match_phrase": {"categories_text": {"query": term}}},
                            {"match_phrase": {"categories_text.tr": {"query": term}}},
                        ],
                        "minimum_should_match": 1,
                    }
                })
    return intent_boost_queries, intent_exclusions


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
                "boost": CONFIG.translation.token_boost,
                "fields": CONFIG.search_methods.multi_match.es_fields,
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
) -> dict:
    """
    Yalnızca kategori adaylarını keşfetmek için size=0 bir aggregation
    sorgusu üretir. Ürün döndürmez; `aggregation_fields` (config'ten) üzerinde
    terms aggregation çalıştırır. `extra_query_texts` ile normalize edilmiş
    sorgu ve/veya en yüksek öncelikli çeviri de eşleşme havuzuna eklenebilir.

    Kullanıcıdan ham alan adı veya ham Query DSL alınmaz; tüm alanlar ve
    limitler config/search_config.json'daki `dynamic_intent` bölümünden gelir.
    """
    dyn = CONFIG.dynamic_intent

    candidate_texts = [text for text in [query_text, *(extra_query_texts or [])] if text]
    should = [
        {
            "multi_match": {
                "query": text,
                "type": "best_fields",
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

    return {
        "size": 0,
        "track_total_hits": False,
        "timeout": f"{dyn.timeout_seconds}s",
        "query": query,
        "aggs": aggs,
    }


@st.cache_data(ttl=_DISCOVERY_CACHE_TTL, show_spinner=False)
def _fetch_category_aggregations(query_text: str, extra_query_texts: tuple[str, ...]):
    """
    Kategori keşif aggregation'ını Elasticsearch'ten getiren cache'li katman.
    TTL, _DISCOVERY_CACHE_TTL (config'teki dynamic_intent.cache_ttl_seconds)
    üzerinden gelir. Kendi cache'i
    vardır; canlı öneri cache'inden (_fetch_suggestion_hits) bağımsızdır.

    Dönüş: (aggregations_dict, hata_mesajı). Hata varsa aggregations_dict {} döner
    — bu, çağıran tarafın (discover_category_intent) hatayı yutup normal
    aramayı engellememesini sağlar; kategori keşfi tek hata noktası olamaz.
    """
    payload = build_category_discovery_query(query_text, list(extra_query_texts))
    data, error = _post_search(payload, timeout=CONFIG.dynamic_intent.timeout_seconds, index=INDEX_NAME)
    if error:
        return {}, error
    return data.get("aggregations", {}), None


def discover_category_intent(query_text: str) -> list[dict]:
    """
    Sorgudan (orijinal + normalize edilmiş + en yüksek öncelikli İngilizce
    çeviri kullanılarak) Elasticsearch aggregation'ları ile kategori adayları
    keşfeder. Önceden `intent_rules.json`da tanımlanmamış tamamen yeni ürün
    tipleri için de (ör. "toilet paper", "gaming mouse", "cat food") çalışır.

    Başarısız olursa (timeout, bağlantı hatası, min. sorgu uzunluğu altında,
    devre dışı) sessizce boş liste döner — normal aramayı ASLA engellemez.

    Dönüş: [{"value": str, "field": str, "doc_count": int, "rank": int,
             "source": "dynamic_category_discovery"}, ...]
    """
    dyn = CONFIG.dynamic_intent
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

    aggregations, error = _fetch_category_aggregations(query_text, tuple(extra_texts))
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


def build_dynamic_category_boosts(candidates: list[dict]) -> list[dict]:
    """
    Kategori keşif adaylarını, ana ürün sorgusunun bool.should (rerank-only)
    kısmına eklenecek boost sorgularına çevirir. `aggregation_fields`
    (categories/main_category/source_category) her zaman keyword-uyumlu
    olduğundan `term` sorgusu kullanılır. Bu sinyaller tek başına belge
    döndürmez; yalnızca zaten lexical olarak eşleşmiş ürünleri yeniden sıralar.
    """
    if not candidates:
        return []

    boost = CONFIG.dynamic_intent.boost
    return [
        {"term": {candidate["field"]: {"value": candidate["value"], "boost": boost}}}
        for candidate in candidates
    ]


def resolve_intent_signals(query_text: str, include_dynamic: bool = True):
    """
    Manuel intent kuralları (`intent_rules.json`) ile dinamik kategori
    keşfini birleştiren ana giriş noktası. `intent_rules.json` artık ana
    motor DEĞİLDİR; yalnızca dinamik keşfin üzerine binen opsiyonel bir
    override katmanıdır:
      - bir kuralın `negative_categories`'i (exclusions) aktifse, dinamik
        keşfin önerdiği aynı değerdeki kategori adayları da elenir
        (override, keşfi çelmeyecek şekilde bastırır).
      - `include_dynamic=False` yalnızca autocomplete tarafından kullanılır
        (dinamik keşif autocomplete'te ÇALIŞMAZ).

    Dönüş: (boost_queries, exclusions) — build_search_query'nin bool.should
    ve bool.must_not'una doğrudan eklenir.
    """
    info = detect_search_intent(query_text)
    boost_queries, exclusions = _build_intent_signals(query_text)

    if include_dynamic and CONFIG.dynamic_intent.enabled:
        rule = info.get("rule")
        blocked_values = set()
        if rule and info.get("apply_exclusion"):
            blocked_values = {value.casefold() for value in rule.negative_categories}

        candidates = discover_category_intent(query_text)
        candidates = [c for c in candidates if str(c["value"]).casefold() not in blocked_values]
        boost_queries = boost_queries + build_dynamic_category_boosts(candidates)

    return boost_queries, exclusions


# ---------------------------------------------------------------------------
# Sorgu oluşturma (canlı öneriler)
# ---------------------------------------------------------------------------
def build_autocomplete_query(
    query_text: str,
    result_size: int | None = None,
    apply_intent_reranking: bool = True,
) -> dict:
    """
    Canlı öneriler için Edge NGram autocomplete indexi sorgusu.

    Normal lexical yöntemlerden (phrase/multi/fuzzy/asin switchleri) bağımsızdır:
    zorunlu eşleşme `title.autocomplete` alanı üzerindendir (operator config'ten
    gelir). Config'te çeviri autocomplete için açıksa, tam ifade çevirileri de
    zorunlu eşleşme grubuna alternatif olarak eklenir (kelime bazlı çeviri,
    kısmi/prefiks sorgularda hatalı genişlemeyi önlemek için eklenmez).
    intent boost/dışlama korunur. Intent sinyalleri tek başına sonuç üretmez;
    autocomplete eşleşmesi zorunludur.
    """
    result_size = result_size if result_size is not None else CONFIG.limits.autocomplete_fetch_size

    autocomplete_field = CONFIG.search_methods.autocomplete.field
    autocomplete_operator = CONFIG.search_methods.autocomplete.operator

    lexical_queries = [
        {
            "match": {
                autocomplete_field: {
                    "query": query_text,
                    "operator": autocomplete_operator,
                }
            }
        }
    ]

    if CONFIG.translation.enabled and CONFIG.translation.autocomplete_enabled:
        expansion = expand_multilingual_query(query_text)
        for phrase in expansion["phrase_translations"]:
            lexical_queries.append({
                "match": {
                    autocomplete_field: {
                        "query": phrase,
                        "operator": autocomplete_operator,
                        "boost": CONFIG.translation.phrase_boost,
                    }
                }
            })

    # Not: yalnızca manuel intent kuralları kullanılır — dinamik kategori
    # keşfi (Elasticsearch aggregation isteği) her tuş vuruşunda ekstra bir
    # istek anlamına geleceğinden BİLEREK autocomplete'e dahil edilmez.
    intent_boost_queries = []
    intent_exclusions = []
    if apply_intent_reranking:
        intent_boost_queries, intent_exclusions = _build_intent_signals(query_text)

    return {
        "size": result_size,
        "track_total_hits": False,
        "timeout": f"{CONFIG.elasticsearch.autocomplete_timeout_seconds}s",
        "_source": SUGGESTION_SOURCE_FIELDS,
        "query": {
            "bool": {
                "must": [
                    {"bool": {"should": lexical_queries, "minimum_should_match": 1}}
                ],
                "should": intent_boost_queries,
                "must_not": intent_exclusions,
            }
        },
    }


# ---------------------------------------------------------------------------
# Sorgu oluşturma (normal arama)
# ---------------------------------------------------------------------------
def build_search_query(
    query_text: str,
    enable_phrase: bool = True,
    enable_multi_match: bool = True,
    enable_fuzzy: bool = True,
    enable_exact_asin: bool = True,
    result_size: int | None = None,
    track_total_hits: bool | None = None,
    apply_intent_reranking: bool = True,
    intent_boost_queries: list[dict] | None = None,
    intent_exclusions: list[dict] | None = None,
) -> dict:
    """
    Seçili yöntemlere göre intent-farkındalıklı bir Elasticsearch sorgusu üretir.
    Alan adları ve boostlar config/search_config.json'daki search_methods'tan
    okunur.

    SAF (I/O yapmaz) bir fonksiyondur — Elasticsearch'e istek atmaz. Dinamik
    kategori keşfi (Elasticsearch aggregation isteği gerektirir) burada değil,
    `search_products` içinde `resolve_intent_signals` ile hesaplanır ve
    sonucu `intent_boost_queries`/`intent_exclusions` olarak bu fonksiyona
    enjekte edilir. Bu ikisi `None` bırakılırsa (varsayılan çağrı biçimi),
    yalnızca manuel `intent_rules.json` kuralları (`_build_intent_signals`,
    saf) kullanılır — böylece bu fonksiyon testlerde ağ çağrısı yapmadan
    doğrudan çağrılabilir.

    Lexical yöntemler (aç/kapa):
      A) parent_asin exact `term`   (enable_exact_asin)
      B) title `match_phrase`        (enable_phrase)
      C) full-text `multi_match`     (enable_multi_match)
      D) fuzzy `multi_match`         (enable_fuzzy)

    Yapı:
      bool.must   → [ bool.should=lexical (+ çeviri alternatifleri), minimum_should_match=1 ]
      bool.should → intent kategori boostları (manuel + dinamik; yalnızca sıralamayı iyileştirir)
      bool.must_not → intent dışlamaları (kontrollü)

    Böylece kategori boostları tek başına belge döndürmez; ürün önce lexical
    (veya çevrilmiş bir lexical alternatif) olarak eşleşmek zorundadır.
    Hiç lexical yöntem yoksa `match_none` döner.
    """
    methods = CONFIG.search_methods
    result_size = result_size if result_size is not None else CONFIG.limits.result_size
    track_total_hits = (
        track_total_hits if track_total_hits is not None else CONFIG.elasticsearch.track_total_hits
    )

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

    # C. Normal full-text multi_match
    if enable_multi_match:
        lexical_queries.append({
            "multi_match": {
                "query": query_text,
                "type": methods.multi_match.type,
                "operator": methods.multi_match.operator,
                "boost": methods.multi_match.boost,
                "fields": methods.multi_match.es_fields,
            }
        })

    # D. Fuzzy multi_match (yazım hataları)
    if enable_fuzzy:
        lexical_queries.append({
            "multi_match": {
                "query": query_text,
                "type": methods.fuzzy.type,
                "fuzziness": methods.fuzzy.fuzziness,
                "prefix_length": methods.fuzzy.prefix_length,
                "max_expansions": methods.fuzzy.max_expansions,
                "boost": methods.fuzzy.boost,
                "fields": methods.fuzzy.es_fields,
            }
        })

    # Hiç lexical yöntem yoksa güvenlik ağı.
    if not lexical_queries:
        return {
            "size": result_size,
            "track_total_hits": track_total_hits,
            "_source": SOURCE_FIELDS,
            "query": {"match_none": {}},
        }

    # Çeviri sözlüğünden gelen alternatifler zorunlu eşleşme grubuna eklenir
    # (bypass etmez — ek bir "veya" seçeneğidir).
    lexical_queries.extend(_build_translation_lexical_queries(query_text))

    # Intent boost/dışlama sinyalleri. Çağıran taraf (search_products) zaten
    # resolve_intent_signals ile manuel+dinamik sinyalleri hesaplayıp enjekte
    # ettiyse onlar kullanılır; aksi halde (varsayılan, saf çağrı) yalnızca
    # manuel kurallar hesaplanır — lexical eşleşme zorunluluğunu değiştirmez.
    if apply_intent_reranking:
        if intent_boost_queries is None and intent_exclusions is None:
            intent_boost_queries, intent_exclusions = _build_intent_signals(query_text)
        else:
            intent_boost_queries = intent_boost_queries or []
            intent_exclusions = intent_exclusions or []
    else:
        intent_boost_queries, intent_exclusions = [], []

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
    if intent_boost_queries:
        bool_query["should"] = intent_boost_queries
    if intent_exclusions:
        bool_query["must_not"] = intent_exclusions

    return {
        "size": result_size,
        "track_total_hits": track_total_hits,
        "_source": SOURCE_FIELDS,
        "query": {"bool": bool_query},
    }


# ---------------------------------------------------------------------------
# Elasticsearch isteği
# ---------------------------------------------------------------------------
def _post_search(payload: dict, timeout: int = 20, index: str = None):
    """
    Verilen sorgu gövdesini belirtilen index üzerinde Elastic Cloud'a gönderir;
    ortak HTTP ve hata yönetimini tek yerde toplar. Varsayılan index normal
    aramanın kullandığı INDEX_NAME'dir.

    Dönüş: (data_dict, hata_mesaji). Hata varsa data_dict None döner.
    """
    if index is None:
        index = INDEX_NAME

    headers = {
        "Authorization": f"ApiKey {ES_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(
            f"{ES_URL}/{index}/_search",
            headers=headers,
            json=payload,
            timeout=timeout,
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
):
    """
    Seçili yöntemlerle üretilen gelişmiş sorguyu Elastic Cloud'a gönderir.

    Normal arama akışının GERÇEK giriş noktasıdır: burada önce
    `resolve_intent_signals` çağrılır (manuel `intent_rules.json` kuralları +
    dinamik kategori keşfi Elasticsearch aggregation isteğini içerir), sonra
    sonuç saf `build_search_query`'ye enjekte edilir. Kategori keşfi
    başarısız olursa (bkz. discover_category_intent) sessizce boş liste
    döner; bu, ana ürün aramasını asla engellemez.

    Dönüş: (hits_listesi, toplam_sonuc, hata_mesaji)
    Hata varsa hits_listesi None döner.
    """
    intent_boost_queries, intent_exclusions = resolve_intent_signals(
        query_text, include_dynamic=True
    )
    payload = build_search_query(
        query_text,
        enable_phrase=enable_phrase,
        enable_multi_match=enable_multi_match,
        enable_fuzzy=enable_fuzzy,
        enable_exact_asin=enable_exact_asin,
        result_size=RESULT_SIZE,
        track_total_hits=True,
        intent_boost_queries=intent_boost_queries,
        intent_exclusions=intent_exclusions,
    )

    data, error = _post_search(payload, timeout=CONFIG.elasticsearch.search_timeout_seconds)
    if error:
        return None, 0, error

    hits = data.get("hits", {}).get("hits", [])
    total = data.get("hits", {}).get("total", {}).get("value", len(hits))
    return hits, total, None


@st.cache_data(ttl=_AUTOCOMPLETE_CACHE_TTL, show_spinner=False)
def _fetch_suggestion_hits(query_text: str, result_size: int):
    """
    Canlı önerileri Edge NGram autocomplete indexinden (AUTOCOMPLETE_INDEX_NAME)
    getiren cache'li katman. TTL, _AUTOCOMPLETE_CACHE_TTL (config'teki
    limits.autocomplete_cache_ttl_seconds) üzerinden gelir. Cache anahtarı
    yalnızca query_text ve result_size'dır; lexical arama switchleri
    önerileri ETKİLEMEZ.

    Cache güvenliği için yalnızca JSON-benzeri argümanlar/dönüşler kullanılır;
    API key ve bağlantı nesneleri parametre yapılmaz (ortam değişkeni globaldir).

    Dönüş: (hits_listesi, hata_mesaji)
    """
    payload = build_autocomplete_query(query_text, result_size=result_size)

    data, error = _post_search(
        payload, timeout=CONFIG.elasticsearch.autocomplete_timeout_seconds, index=AUTOCOMPLETE_INDEX_NAME
    )
    if error:
        return [], error
    return data.get("hits", {}).get("hits", []), None


def get_suggestions(query_text: str, max_items: int | None = None):
    """
    Edge NGram autocomplete önerileri: title.autocomplete alanı üzerinden
    _score sıralı ürün önerileri (intent boost/dışlama ve çeviri alternatifleri
    korunur).

    Elasticsearch'ten config'teki autocomplete_fetch_size kadar hit istenir,
    Python'da benzersizleştirilir (parent_asin, yoksa title.casefold()) ve en
    fazla `max_items` (varsayılan: config'teki autocomplete_display_size)
    öneri döndürülür. Öneriler lexical arama switchlerinden bağımsızdır.

    Dönüş: (suggestions, error)
      - başarılı: (liste, None)
      - hata:     ([], "hata mesajı")
    """
    max_items = max_items if max_items is not None else CONFIG.limits.autocomplete_display_size
    hits, error = _fetch_suggestion_hits(query_text, CONFIG.limits.autocomplete_fetch_size)
    if error:
        return [], error

    suggestions = []
    seen = set()
    for hit in hits:
        src = hit.get("_source", {})
        asin = src.get("parent_asin")
        title = src.get("title")

        key = asin if asin else (title.casefold() if title else None)
        if key is None or key in seen:
            continue
        seen.add(key)

        suggestions.append({
            "title": title or "Ürün adı bulunamadı",
            "asin": asin or "",
            "store": src.get("store"),
            "price": src.get("price"),
            "average_rating": src.get("average_rating"),
            "image_url": src.get("image_url"),
            "score": hit.get("_score", 0) or 0,
        })
        if len(suggestions) >= max_items:
            break

    return suggestions, None


# ---------------------------------------------------------------------------
# Tıklanabilir kart (components.v1.html ile)
# ---------------------------------------------------------------------------
def render_product_card(hit: dict):
    """
    Tek bir ürün kartını tıklanabilir bir HTML bileşeni olarak render eder.
    Kartın tamamı link gibi davranır; tıklama yeni sekmede Amazon ürününü açar.
    """
    source = hit.get("_source", {})
    score = hit.get("_score", 0) or 0

    # ASIN: önce parent_asin, yoksa _id
    asin = source.get("parent_asin") or hit.get("_id") or ""
    product_url = CONFIG.product_url_template.format(asin=asin) if asin else ""

    title = source.get("title") or "Ürün adı bulunamadı"
    store = source.get("store") or "Bilinmiyor"
    main_category = source.get("main_category") or "Kategori yok"
    average_rating = source.get("average_rating")
    rating_number = source.get("rating_number")
    price = source.get("price")
    image_url = source.get("image_url") or PLACEHOLDER_IMAGE

    # Tüm değerleri güvenli hale getir
    e_url = html.escape(product_url, quote=True)
    e_title = html.escape(str(title))
    e_store = html.escape(str(store))
    e_category = html.escape(str(main_category))
    e_image = html.escape(str(image_url), quote=True)
    e_placeholder = html.escape(PLACEHOLDER_IMAGE, quote=True)

    if average_rating is not None:
        rating_text = (
            f"⭐ {html.escape(str(average_rating))} &nbsp;·&nbsp; "
            f"{html.escape(str(rating_number or 0))} değerlendirme"
        )
    else:
        rating_text = "Değerlendirme yok"

    score_text = f"Skor: {score:.2f}"

    # Fiyat: yalnızca geçerli bir sayı varsa göster, yoksa hata verme.
    price_html = ""
    try:
        if price is not None and float(price) > 0:
            price_html = f'<div class="product-price">💲 {float(price):.2f}</div>'
    except (TypeError, ValueError):
        price_html = ""

    # JS onclick içine güvenli biçimde gömmek için tek tırnak kaçışı
    js_url = e_url.replace("'", "\\'")

    card_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
    <meta charset="utf-8">
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{ background: transparent; font-family: -apple-system, BlinkMacSystemFont,
                "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }}

        .product-card {{
            display: flex;
            gap: 20px;
            background: #ffffff;
            border: 1px solid #e5e7eb;
            border-radius: 12px;
            padding: 18px;
            box-shadow: 0 1px 3px rgba(0, 0, 0, 0.06);
            transition: box-shadow 0.2s ease, transform 0.2s ease;
            cursor: pointer;
            text-decoration: none;
            outline: none;
        }}
        .product-card:hover {{
            box-shadow: 0 6px 18px rgba(0, 0, 0, 0.14);
            transform: translateY(-2px);
        }}
        .product-card:focus-visible {{
            box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.5);
        }}

        .product-image {{
            width: 140px;
            height: 140px;
            flex-shrink: 0;
            border-radius: 8px;
            object-fit: contain;
            background: #f9fafb;
            border: 1px solid #f3f4f6;
        }}

        .product-info {{ flex: 1; min-width: 0; }}
        /* Başlık düz koyu metin — link değil, alt çizgi/mavi renk YOK */
        .product-title {{
            font-size: 1.05rem;
            font-weight: 600;
            color: #111827;
            margin-bottom: 8px;
            line-height: 1.4;
            text-decoration: none;
        }}
        .product-meta {{
            color: #6b7280;
            font-size: 0.88rem;
            margin-bottom: 4px;
        }}
        .product-rating {{
            color: #374151;
            font-size: 0.9rem;
            margin-top: 6px;
        }}
        .product-price {{
            color: #059669;
            font-weight: 600;
            font-size: 0.95rem;
            margin-top: 4px;
        }}
        .score-badge {{
            display: inline-block;
            background: #f3f4f6;
            color: #4b5563;
            font-size: 0.78rem;
            padding: 2px 10px;
            border-radius: 999px;
            margin-top: 8px;
        }}
    </style>
    </head>
    <body>
        <div class="product-card" role="link" tabindex="0"
             aria-label="{e_title}"
             onclick="openProduct()"
             onkeydown="if(event.key==='Enter'||event.key===' '){{event.preventDefault();openProduct();}}">
            <img class="product-image" src="{e_image}"
                 onerror="this.onerror=null;this.src='{e_placeholder}'" alt="Ürün görseli">
            <div class="product-info">
                <div class="product-title">{e_title}</div>
                <div class="product-meta">🏬 Mağaza: {e_store}</div>
                <div class="product-meta">📂 Kategori: {e_category}</div>
                <div class="product-rating">{rating_text}</div>
                {price_html}
                <span class="score-badge">{score_text}</span>
            </div>
        </div>
        <script>
            function openProduct() {{
                var url = '{js_url}';
                if (url) {{ window.open(url, "_blank", "noopener,noreferrer"); }}
            }}
        </script>
    </body>
    </html>
    """

    # Kart yüksekliği: 140px görsel + iç boşluk + (varsa) fiyat satırı + kart altı boşluk.
    card_height = 224 if price_html else 196
    components.html(card_html, height=card_height, scrolling=False)


# ---------------------------------------------------------------------------
# Arayüz yardımcıları
# ---------------------------------------------------------------------------
def _suggestion_visual_html(item: dict) -> tuple:
    """Öneri satırının sol görsel kısmını (kompakt kart) HTML olarak üretir."""
    title = item.get("title") or "Ürün adı bulunamadı"
    store = item.get("store") or "Mağaza bilgisi yok"
    price = item.get("price")
    rating = item.get("average_rating")
    image_url = item.get("image_url") or PLACEHOLDER_IMAGE
    score = item.get("score", 0) or 0

    e_title = html.escape(str(title))
    e_store = html.escape(str(store))
    e_image = html.escape(str(image_url), quote=True)
    e_placeholder = html.escape(PLACEHOLDER_IMAGE, quote=True)

    price_html = ""
    try:
        if price is not None and float(price) > 0:
            price_html = f'<span class="s-price">💲 {float(price):.2f}</span>'
    except (TypeError, ValueError):
        price_html = ""

    rating_html = ""
    if rating is not None:
        rating_html = f'<span class="s-rating">⭐ {html.escape(str(rating))}</span>'

    meta_line = " · ".join(
        p for p in [f"🏬 {e_store}", rating_html, price_html] if p
    )

    card_html = f"""
    <!DOCTYPE html>
    <html><head><meta charset="utf-8"><style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{ background: transparent; font-family: -apple-system, BlinkMacSystemFont,
                "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }}
        .s-card {{
            display: flex; gap: 12px; align-items: center;
            background: #ffffff; border: 1px solid #e5e7eb; border-radius: 10px;
            padding: 8px 12px;
        }}
        .s-image {{
            width: 48px; height: 48px; flex-shrink: 0; object-fit: contain;
            border-radius: 6px; background: #f9fafb; border: 1px solid #f3f4f6;
        }}
        .s-info {{ flex: 1; min-width: 0; }}
        .s-title {{
            font-size: 0.9rem; font-weight: 600; color: #111827; line-height: 1.3;
            display: -webkit-box; -webkit-line-clamp: 1; -webkit-box-orient: vertical;
            overflow: hidden;
        }}
        .s-metaline {{ font-size: 0.76rem; color: #6b7280; margin-top: 2px; }}
        .s-price {{ color: #059669; font-weight: 600; }}
        .s-rating {{ color: #374151; }}
        .s-score {{
            display: inline-block; background: #f3f4f6; color: #4b5563;
            font-size: 0.68rem; padding: 1px 8px; border-radius: 999px; margin-top: 3px;
        }}
    </style></head><body>
        <div class="s-card">
            <img class="s-image" src="{e_image}"
                 onerror="this.onerror=null;this.src='{e_placeholder}'" alt="">
            <div class="s-info">
                <div class="s-title">{e_title}</div>
                <div class="s-metaline">{meta_line}</div>
                <span class="s-score">Arama skoru: {score:.2f}</span>
            </div>
        </div>
    </body></html>
    """
    return card_html, 88


def render_suggestion_row(item: dict, idx: int) -> bool:
    """
    Tek bir öneriyi kompakt biçimde çizer: solda görsel/bilgi, sağda iki eylem.
      - "Bu ürünü ara": inputu bu başlıkla doldurup normal aramayı çalıştırır.
      - "Amazon'da aç": parent_asin varsa yeni sekmede ürün sayfasını açar.
    Dönüş: "Bu ürünü ara" tıklandıysa True.
    """
    card_html, height = _suggestion_visual_html(item)
    col_card, col_actions = st.columns([4, 1.15])
    with col_card:
        components.html(card_html, height=height, scrolling=False)
    with col_actions:
        picked = st.button(
            CONFIG.ui.label("suggestion_pick_button", "🔎 Bu ürünü ara"),
            key=f"pick_{idx}",
            use_container_width=True,
        )
        asin = item.get("asin") or ""
        if asin:
            amazon_url = CONFIG.product_url_template.format(asin=asin)
            open_label = CONFIG.ui.label("suggestion_open_amazon_button", "Amazon'da aç")
            if hasattr(st, "link_button"):
                st.link_button(open_label, amazon_url, use_container_width=True)
            else:
                # Güvenli HTML anchor (yeni sekme).
                safe_url = html.escape(amazon_url, quote=True)
                st.markdown(
                    f'<a href="{safe_url}" target="_blank" rel="noopener noreferrer">'
                    f"{html.escape(open_label)}</a>",
                    unsafe_allow_html=True,
                )
    return picked


def _toggle(label: str, value: bool, key: str, help_text: str = "") -> bool:
    """st.toggle varsa onu, yoksa st.checkbox'ı kullanır (sürüm uyumu)."""
    if hasattr(st, "toggle"):
        val = st.sidebar.toggle(label, value=value, key=key)
    else:
        val = st.sidebar.checkbox(label, value=value, key=key)
    if help_text:
        st.sidebar.caption(help_text)
    return val


def render_search_settings():
    """
    Sidebar'daki "Arama Ayarları" paneli: her switch'in altında bir açıklama
    ve altta küçük bir sistem durumu alanı. Dönüş: (flags, live_suggestions)
    """
    ui = CONFIG.ui
    st.sidebar.markdown(f"### {ui.label('settings_panel_title', 'Arama Ayarları')}")
    enable_phrase = _toggle(
        ui.label("toggle_phrase"), True, "opt_phrase", ui.help("toggle_phrase"),
    )
    enable_multi = _toggle(
        ui.label("toggle_multi_match"), True, "opt_multi", ui.help("toggle_multi_match"),
    )
    enable_fuzzy = _toggle(
        ui.label("toggle_fuzzy"), True, "opt_fuzzy", ui.help("toggle_fuzzy"),
    )
    enable_asin = _toggle(
        ui.label("toggle_exact_asin"), True, "opt_asin", ui.help("toggle_exact_asin"),
    )
    st.sidebar.markdown("---")
    live_suggestions = _toggle(
        ui.label("toggle_live_suggestions"), True, "opt_suggest", ui.help("toggle_live_suggestions"),
    )

    flags = {
        "enable_phrase": enable_phrase,
        "enable_multi_match": enable_multi,
        "enable_fuzzy": enable_fuzzy,
        "enable_exact_asin": enable_asin,
    }

    # Sistem durumu (gerçek bağlantı doğrulanmadan "Bağlı" denmez).
    st.sidebar.markdown("---")
    st.sidebar.markdown(f"#### {ui.label('system_status_title', 'Sistem Durumu')}")
    status_key = "status_connected" if st.session_state.get("es_ok") else "status_ready"
    es_state = ui.label(status_key)
    st.sidebar.caption(
        ui.message(
            "system_status_caption",
            status=es_state,
            index_count=len(CONFIG.elasticsearch.search_indices),
            result_size=RESULT_SIZE,
            suggestion_limit=CONFIG.limits.autocomplete_display_size,
        )
    )
    return flags, live_suggestions


# Sidebar switch anahtarları ile config'teki chip/method etiket anahtarlarının eşleşmesi.
_FLAG_CHIP_KEYS = {
    "enable_phrase": "chip_phrase",
    "enable_multi_match": "chip_multi_match",
    "enable_fuzzy": "chip_fuzzy",
    "enable_exact_asin": "chip_exact_asin",
}
_FLAG_METHOD_KEYS = {
    "enable_phrase": "method_phrase",
    "enable_multi_match": "method_multi_match",
    "enable_fuzzy": "method_fuzzy",
    "enable_exact_asin": "method_exact_asin",
}


def render_feature_chips(flags: dict, live_suggestions: bool):
    """Arama kutusunun altında aktif özellikleri chip olarak gösterir."""
    ui = CONFIG.ui
    chips = []
    for key, chip_key in _FLAG_CHIP_KEYS.items():
        if flags.get(key):
            chips.append(f'<span class="chip chip-on">{ui.label(chip_key)}</span>')
    if live_suggestions:
        chips.append(f'<span class="chip chip-on">{ui.label("chip_live_suggestions")}</span>')
    if not chips:
        chips.append(f'<span class="chip">{ui.label("chip_none", "Aktif yöntem yok")}</span>')
    st.markdown(
        f'<div class="chip-row">{"".join(chips)}</div>', unsafe_allow_html=True
    )


def render_hero():
    """Üst hero/search bölümü başlığı."""
    ui = CONFIG.ui
    st.markdown(
        f"""
        <div class="hero">
            <div class="hero-logo">{ui.hero_logo}</div>
            <div class="hero-title">{html.escape(ui.hero_title)}</div>
            <div class="hero-subtitle">
                {html.escape(ui.hero_subtitle)}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_result_header(query_text, total, shown, flags, intent_name):
    """Sonuçların üzerinde sorgu, toplam/gösterilen sayı, yöntemler ve intent."""
    ui = CONFIG.ui
    e_query = html.escape(query_text)
    methods = ", ".join(
        ui.label(_FLAG_METHOD_KEYS[k]) for k, v in flags.items() if v
    ) or "yok"

    intent_html = ""
    if intent_name and intent_name in INTENT_RULES:
        rule = INTENT_RULES[intent_name]
        icon = rule.icon or ui.intent_fallback_icon
        intent_html = f'<div class="intent-badge">{icon} {html.escape(rule.label)}</div>'

    query_line = ui.message("result_header_query", query=e_query, total=total)
    meta_line = ui.message("result_header_meta", shown=shown, methods=methods)

    st.markdown(
        f"""
        <div class="result-header">
            <div class="rh-query">{query_line}</div>
            <div class="rh-meta">{meta_line}</div>
            {intent_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def _select_query(new_value: str):
    """Bir başlık/örnek seçildiğinde inputu doldurup normal aramayı tetikler."""
    st.session_state["pending_value"] = new_value
    st.session_state["query_widget_version"] = (
        st.session_state.get("query_widget_version", 0) + 1
    )
    st.session_state["run_search"] = True
    st.session_state["hide_suggestions_once"] = True


def render_empty_state():
    """Arama yapılmadan önce gösterilen canlı boş ekran + örnek sorgu chip'leri."""
    ui = CONFIG.ui
    st.markdown(
        f"""
        <div class="empty-state">
            <div class="es-icon">{ui.label('empty_state_icon', '🔍')}</div>
            <div class="es-text">{html.escape(ui.label('empty_state_text'))}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    example_queries = CONFIG.ui.example_queries
    cols = st.columns(len(example_queries))
    for col, example in zip(cols, example_queries):
        with col:
            if st.button(example, key=f"ex_{example}", use_container_width=True):
                _select_query(example)
                st.rerun()


def _make_search_input(widget_key: str, default_val: str) -> str:
    """
    Arama kutusunu oluşturur. st_keyup varsa gerçek tuş-bazlı (config'teki
    debounce_ms kadar debounce'lı) input; yoksa düz st.text_input
    (Enter/odak ile) kullanılır.
    """
    if HAS_KEYUP:
        return st_keyup(
            "Ürün ara",
            value=default_val,
            debounce=CONFIG.ui.debounce_ms,
            key=widget_key,
            label_visibility="collapsed",
            placeholder=CONFIG.ui.search_placeholder,
        )

    def _cb():
        st.session_state["run_search"] = True

    return st.text_input(
        "Ürün ara",
        value=default_val,
        key=widget_key,
        label_visibility="collapsed",
        placeholder=CONFIG.ui.search_placeholder,
        on_change=_cb,
    )


# ---------------------------------------------------------------------------
# Arayüz
# ---------------------------------------------------------------------------
def main():
    render_hero()

    config_error = check_configuration()
    if config_error:
        st.error(config_error)
        return

    ui = CONFIG.ui
    flags, live_suggestions = render_search_settings()
    any_method_on = any(flags.values())
    settings_sig = tuple(sorted(flags.items()))

    # Session defaults
    st.session_state.setdefault("query_widget_version", 0)
    st.session_state.setdefault("run_search", False)

    # Arama kutusu + Ara butonu. Widget key versiyonlanır ki öneri/örnek seçimi
    # inputu güvenle güncelleyebilsin (DuplicateWidgetID / state döngüsü olmadan).
    version = st.session_state["query_widget_version"]
    widget_key = f"search_box_v{version}"
    default_val = st.session_state.get("pending_value", "")

    col_input, col_button = st.columns([5, 1])
    with col_input:
        typed = _make_search_input(widget_key, default_val)
    with col_button:
        if st.button(ui.label("search_button", "Ara"), type="primary", use_container_width=True):
            st.session_state["run_search"] = True
    # pending_value yalnızca yeni widget'ın default'u olarak bir kez kullanılır.
    st.session_state.pop("pending_value", None)

    query_text = (typed or "").strip()
    st.session_state["current_query"] = query_text

    render_feature_chips(flags, live_suggestions)

    # -----------------------------------------------------------------------
    # Canlı öneriler — Edge NGram autocomplete indexi (title.autocomplete).
    # Lexical arama switchlerinden BAĞIMSIZDIR; tüm lexical yöntemler kapalı
    # olsa bile "Canlı öneriler" açıksa ve sorgu config'teki minimum karakter
    # sayısına eriştiyse çalışır. st_keyup ile input değeri debounce sonrası
    # backend'e ulaştığında ilk N (config'teki autocomplete_display_size)
    # benzersiz öneri gösterilir. Intent boost/dışlama ve çeviri alternatifleri
    # korunur.
    # -----------------------------------------------------------------------
    min_chars = CONFIG.limits.autocomplete_min_chars
    show_suggestions = live_suggestions and not st.session_state.pop(
        "hide_suggestions_once", False
    )
    if show_suggestions:
        if query_text and len(query_text) < min_chars:
            st.caption(ui.message("suggestions_min_chars_caption", min_chars=min_chars))
        elif len(query_text) >= min_chars:
            suggestions, sug_error = get_suggestions(query_text)
            if sug_error:
                st.warning(ui.message("suggestions_unavailable_warning"))
            elif suggestions:
                st.markdown(
                    f'<div class="suggest-panel-title">{html.escape(ui.label("suggest_panel_title"))}</div>',
                    unsafe_allow_html=True,
                )
                for idx, item in enumerate(suggestions):
                    if render_suggestion_row(item, idx):
                        # Öneri seçildi: inputu doldur, normal aramayı çalıştır,
                        # önerileri kapat. Amazon otomatik açılmaz.
                        _select_query(item["title"])
                        st.rerun()
                st.caption(ui.message("score_disclaimer_caption"))

    # -----------------------------------------------------------------------
    # Normal arama tetikleyicisi
    # -----------------------------------------------------------------------
    triggered = st.session_state.get("run_search", False)

    if triggered:
        st.session_state["run_search"] = False  # tek seferlik tüket
        if not query_text:
            st.warning(ui.message("empty_query_warning"))
        elif not any_method_on:
            # Normal arama için en az bir lexical yöntem gerekir; öneriler
            # yukarıda bağımsız çalışmaya devam eder.
            st.warning(ui.message("no_method_warning"))
        else:
            with st.spinner("Ürünler aranıyor..."):
                hits, total, error = search_products(query_text, **flags)
            # Sonuçları sakla (rerun'larda kaybolmasın); ayar imzasını da tut.
            st.session_state["search_query"] = query_text
            st.session_state["search_hits"] = hits
            st.session_state["search_total"] = total
            st.session_state["search_error"] = error
            st.session_state["search_sig"] = settings_sig
            if not error and hits:
                st.session_state["es_ok"] = True

    # -----------------------------------------------------------------------
    # Sonuç gösterimi (saklı sonuçlardan). Ayarlar değiştiyse eski ayarların
    # sonucunu yeni ayarlar altında sessizce gösterme.
    # -----------------------------------------------------------------------
    has_stored = "search_hits" in st.session_state
    if has_stored and st.session_state.get("search_sig") != settings_sig:
        # Ayarlar değişti: bayat sonuçları temizle.
        for k in ("search_hits", "search_total", "search_error",
                  "search_query", "search_sig"):
            st.session_state.pop(k, None)
        has_stored = False
        st.info(ui.message("settings_changed_info"))

    if not has_stored:
        if not query_text:
            render_empty_state()
        return

    error = st.session_state.get("search_error")
    if error:
        st.error(error)
        return

    hits = st.session_state.get("search_hits") or []
    total = st.session_state.get("search_total", 0)
    stored_query = st.session_state.get("search_query", query_text)

    if not hits:
        st.info(ui.message("no_results_info", query=stored_query))
        return

    intent_name = detect_search_intent(stored_query).get("intent")
    render_result_header(stored_query, total, len(hits), flags, intent_name)

    for hit in hits:
        render_product_card(hit)


if __name__ == "__main__":
    main()
