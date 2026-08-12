"""
Uygulama yapılandırmasını (arama ayarları, intent kuralları, çeviri sözlüğü)
JSON dosyalarından güvenli biçimde yükler ve doğrular.

Sırlar (ELASTICSEARCH_URL, ELASTICSEARCH_API_KEY) burada değil; onlar yalnızca
ortam değişkenlerinden okunur (bkz. app.py).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).parent
CONFIG_DIR = BASE_DIR / "config"

SEARCH_CONFIG_PATH = CONFIG_DIR / "search_config.json"
INTENT_RULES_PATH = CONFIG_DIR / "intent_rules.json"
TRANSLATIONS_PATH = CONFIG_DIR / "query_translations.json"


class ConfigError(Exception):
    """Yapılandırma dosyaları okunamadığında veya geçersiz olduğunda fırlatılır."""


# ============================================================
# YARDIMCI DOĞRULAMA FONKSİYONLARI
# ============================================================

def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except json.JSONDecodeError as error:
        raise ConfigError(f"{path} geçerli bir JSON dosyası değil: {error}") from error

    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path} bir JSON nesnesi (obje) içermeli.")
    return data


def _require_dict(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{context} bir JSON nesnesi olmalı, alınan: {type(value).__name__}")
    return value


def _require_str(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{context} boş olmayan bir metin olmalı, alınan: {value!r}")
    return value


def _require_str_list(value: Any, context: str, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"{context} metin listesi olmalı, alınan: {value!r}")
    if not allow_empty and not value:
        raise ConfigError(f"{context} en az bir eleman içermeli.")
    return tuple(value)


def _require_positive_number(value: Any, context: str, allow_zero: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{context} sayı olmalı, alınan: {value!r}")
    if value < 0 or (not allow_zero and value == 0):
        raise ConfigError(f"{context} pozitif olmalı, alınan: {value!r}")
    return float(value)


def _require_positive_int(value: Any, context: str) -> int:
    number = _require_positive_number(value, context)
    if number != int(number):
        raise ConfigError(f"{context} tam sayı olmalı, alınan: {value!r}")
    return int(number)


def _require_bool(value: Any, context: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{context} true/false olmalı, alınan: {value!r}")
    return value


def _env_int_override(env_key: str, default: int) -> int:
    """Ortam değişkeni tanımlıysa JSON değerini geçersiz kılar (env > JSON)."""
    raw = os.getenv(env_key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as error:
        raise ConfigError(f"{env_key} tam sayı olmalı, alınan: {raw!r}") from error


# ============================================================
# VERİ SINIFLARI
# ============================================================

@dataclass(frozen=True)
class ElasticsearchConfig:
    search_indices: tuple[str, ...]
    autocomplete_indices: tuple[str, ...]
    search_timeout_seconds: int
    autocomplete_timeout_seconds: int
    track_total_hits: bool
    # search_indices birden fazla shard'a yayılıyor; ES varsayılan
    # query_then_fetch her shard'ın IDF'ini KENDİ terim istatistiğinden
    # hesaplar, bu da az sayıda shard'da aynı terimin shard'lar arasında
    # çok farklı IDF alıp alakasız sonuçların öne çıkmasına yol açabilir.
    # dfs_query_then_fetch global terim istatistiği toplayıp bu sapmayı
    # giderir (bkz. arama alaka sorunları — CLAUDE.md relevance notları).
    use_dfs_query_then_fetch: bool

    @property
    def search_index_expr(self) -> str:
        return ",".join(self.search_indices)

    @property
    def autocomplete_index_expr(self) -> str:
        return ",".join(self.autocomplete_indices)


@dataclass(frozen=True)
class LimitsConfig:
    result_size: int
    autocomplete_fetch_size: int
    autocomplete_display_size: int
    autocomplete_min_chars: int
    autocomplete_cache_ttl_seconds: int


@dataclass(frozen=True)
class FieldBoost:
    field: str
    boost: float


@dataclass(frozen=True)
class MultiFieldQueryConfig:
    type: str
    boost: float
    fields: tuple[FieldBoost, ...]
    operator: str | None = None
    fuzziness: str | None = None
    prefix_length: int | None = None
    max_expansions: int | None = None

    @property
    def es_fields(self) -> list[str]:
        return [f"{item.field}^{item.boost}" for item in self.fields]


@dataclass(frozen=True)
class AutocompleteQueryConfig:
    field: str
    operator: str


@dataclass(frozen=True)
class SearchMethodsConfig:
    exact_asin: FieldBoost
    phrase: FieldBoost
    fuzzy: MultiFieldQueryConfig
    autocomplete: AutocompleteQueryConfig


@dataclass(frozen=True)
class TranslationConfig:
    enabled: bool
    autocomplete_enabled: bool
    source_language: str
    target_language: str
    max_variants: int
    phrase_boost: float
    token_boost: float
    min_query_length: int
    cache_ttl_seconds: int
    file: str


@dataclass(frozen=True)
class DynamicIntentConfig:
    """Elasticsearch aggregation tabanlı dinamik kategori keşfi ayarları.

    Bu, `intent_rules.json`daki manuel kuralların yerini almaz; aksine
    manuel kurallar bu keşfin üzerine opsiyonel bir override katmanı olarak
    biner (bkz. app.py: resolve_intent_signals).
    """

    enabled: bool
    cache_ttl_seconds: int
    max_category_candidates: int
    minimum_query_length: int
    aggregation_size: int
    boost: float
    timeout_seconds: int
    search_fields: tuple[FieldBoost, ...]
    aggregation_fields: tuple[str, ...]

    @property
    def es_search_fields(self) -> list[str]:
        return [f"{item.field}^{item.boost}" for item in self.search_fields]

    @property
    def aggregation_bucket_map(self) -> dict[str, str]:
        """agg adı -> gerçek Elasticsearch alan adı."""
        return {f"by_{item.replace('.', '_')}": item for item in self.aggregation_fields}


@dataclass(frozen=True)
class QualityRankingConfig:
    """Ürün veri kalitesi (`product_quality.py`) sinyallerinin arama sırasına
    nasıl bağlanacağını kontrol eder. `enabled=false` iken (varsayılan —
    production index'lerinde henüz kalite alanları yok) query-building hiçbir
    şey değiştirmez; alanlar mevcut olmayan eski belgelerde de sorgu asla
    bozulmaz (bkz. app.py: `_build_quality_ranking_functions`)."""

    enabled: bool
    score_field: str
    consistency_field: str
    boost: float
    consistency_boost: float
    low_consistency_threshold: float
    low_consistency_penalty: float
    bypass_for_exact_asin: bool
    missing_value_behavior: str
    discovery_filter_enabled: bool
    discovery_min_data_quality_score: float


@dataclass(frozen=True)
class TitleRankingConfig:
    """Title alanı için katmanlı skor ayarları. Yalnızca burada tanımlı iki
    YENİ katmanı (exact, prefix) kontrol eder — phrase/normal/fuzzy
    katmanları hâlâ `search_methods`'tan (değişmeden) beslenir; bkz. spec
    §5. `enabled=false` iken bu iki katman hiç eklenmez, mevcut davranış
    aynen sürer."""

    enabled: bool
    exact_field: str
    exact_boost: float
    prefix_boost: float
    prefix_max_expansions: int


@dataclass(frozen=True)
class IntentRankingConfig:
    """Kategori boost/penalty'nin toplam etkisini sınırlayan tavan/taban
    değerleri (bkz. spec §6-7). `manual_category_boost_cap` ve
    `dynamic_category_boost_cap` her kaynağın (manuel/dinamik) topladığı
    boost'ları, tavanı aşarsa oransal olarak küçültür.
    `negative_penalty_floor`, soft negatif kategori penalty'sinin
    (0-1 çarpan) hiçbir zaman bu değerin altına inemeyeceği bir taban
    koyar — sonuç asla tamamen silinmez."""

    enabled: bool
    manual_category_boost_cap: float
    dynamic_category_boost_cap: float
    negative_penalty_floor: float


@dataclass(frozen=True)
class CategoryBoostEntry:
    value: str
    boost: float


@dataclass(frozen=True)
class CategoryPenaltyEntry:
    value: str
    penalty: float


@dataclass(frozen=True)
class FieldRelevanceConfig:
    """`title`/`features`/`description`/`categories_text` (+ `.tr`) üzerinde
    query-time relevance için tekli-alan `match` madde ağırlıkları. Eski
    `search_methods.multi_match`in YERİNE geçer (bkz. spec §3) — dis_max
    yerine ADDİTİF (`bool.should` toplamı) puanlanır, bu da field consensus'u
    (bkz. FieldConsensusConfig) mümkün kılan şeydir. `store` BİLEREK bu
    listede yer almaz (bkz. spec §3/§6) — yalnızca ayrı, düşük ağırlıklı
    `store_boost` üzerinden katkı sağlar, hiçbir zorunlu-eşleşme veya
    consensus/contradiction sayımına dahil edilmez."""

    enabled: bool
    operator: str
    fields: tuple[FieldBoost, ...]
    cross_fields_boost: float
    store_boost: float

    @property
    def es_fields(self) -> list[str]:
        return [f"{item.field}^{item.boost}" for item in self.fields]

    @property
    def canonical_fields(self) -> tuple[str, ...]:
        """`.tr` soneki çıkarılmış, tekilleştirilmiş, sıra korunmuş alan
        adları (`title` ve `title.tr` TEK bir `"title"` girdisi olur) —
        böylece bir Türkçe/İngilizce dil çifti consensus/contradiction
        sayımında iki ayrı alan gibi görünmez (bkz. spec §4)."""
        seen: list[str] = []
        for item in self.fields:
            base = item.field[: -len(".tr")] if item.field.endswith(".tr") else item.field
            if base not in seen:
                seen.append(base)
        return tuple(seen)


@dataclass(frozen=True)
class FieldConsensusConfig:
    """Sorgunun BAĞIMSIZ birden fazla alanda GERÇEKTEN eşleştiği (var
    olmakla YETİNMEDİĞİ — bkz. spec §4 düzeltmesi) belgeler için kademeli
    çarpan. `counted_fields`, hangi kanonik alanların bu sayıma dahil
    olduğunu açıkça listeler (`store` asla burada değildir)."""

    enabled: bool
    two_field_boost: float
    three_field_boost: float
    four_plus_field_boost: float
    counted_fields: tuple[str, ...]


@dataclass(frozen=True)
class RelevanceContradictionConfig:
    """`intent_rules.json`daki `contradiction_terms`in (bkz. IntentRule),
    `counted_fields`teki alanlarda GERÇEKTEN eşleştiği (yine `exists` DEĞİL
    — bkz. spec §5 düzeltmesi) belge sayısına göre kademeli penalty.
    `title` BİLEREK `counted_fields` dışındadır — title zaten yanlış-pozitif
    riskini yaratan alan, kontrol edilen 'diğer' alanlardır."""

    enabled: bool
    minimum_conflicting_fields: int
    strong_conflicting_fields: int
    mild_penalty: float
    strong_penalty: float
    minimum_penalty: float
    counted_fields: tuple[str, ...]


@dataclass(frozen=True)
class RelevanceDebugConfig:
    """`debug_relevance=true` altında ES'in native `_name`/`matched_queries`
    mekanizmasından (bkz. spec §8, canlı cluster'da doğrulandı) türetilen
    per-hit debug bilgisinin kaç hit için üretileceğini sınırlar."""

    explain_top_n: int


@dataclass(frozen=True)
class PaginationConfig:
    """Basit `from + size` tabanlı sayfalama ayarları.

    `page_size`, pagination aktifken arama isteğinin `size` değerini
    belirler ve bu durumda `limits.result_size`'ın yerini alır (result_size
    yalnızca `enabled=false` iken kullanılır) — bu öncelik kuralı sayesinde
    iki alan asla birbiriyle çelişmez, her zaman tek bir değer geçerlidir
    (bkz. app.py: build_search_query).

    Elasticsearch'in varsayılan `index.max_result_window` sınırı nedeniyle
    `from + size`, `max_result_window`'ı aşamaz; aşan istekler Elasticsearch'e
    hiç gönderilmez (bkz. app.py: PaginationLimitError). İleride bu sınırın
    ötesine geçmek gerekirse `search_after` tabanlı imleçli sayfalamaya
    geçilebilir.
    """

    enabled: bool
    page_size: int
    max_result_window: int
    max_visible_pages: int

    @property
    def max_allowed_page(self) -> int:
        """`max_result_window` içinde erişilebilecek son sayfa numarası."""
        return max(1, self.max_result_window // self.page_size)


@dataclass(frozen=True)
class SourceFieldsConfig:
    search: tuple[str, ...]
    suggestions: tuple[str, ...]


@dataclass(frozen=True)
class AutocompleteUIConfig:
    """Chrome/Google-tarzı tek autocomplete dropdown panelinin görsel
    ayarları (bkz. components/search_input). Öneri SAYISI burada değil,
    `limits.autocomplete_display_size`'da kontrol edilir — bu bölüm yalnızca
    panelin nasıl göründüğünü belirler."""

    panel_max_height_px: int
    row_height_px: int
    show_images: bool


@dataclass(frozen=True)
class UIConfig:
    hero_logo: str
    hero_title: str
    hero_subtitle: str
    search_placeholder: str
    placeholder_image: str
    example_queries: tuple[str, ...]
    debounce_ms: int
    intent_fallback_icon: str
    labels: dict[str, str] = field(default_factory=dict)
    help_text: dict[str, str] = field(default_factory=dict)
    messages: dict[str, str] = field(default_factory=dict)

    def label(self, key: str, default: str = "") -> str:
        return self.labels.get(key, default)

    def help(self, key: str, default: str = "") -> str:
        return self.help_text.get(key, default)

    def message(self, key: str, **kwargs: Any) -> str:
        template = self.messages.get(key, "")
        if not kwargs:
            return template
        return template.format(**kwargs)


@dataclass(frozen=True)
class AppConfig:
    elasticsearch: ElasticsearchConfig
    limits: LimitsConfig
    search_methods: SearchMethodsConfig
    translation: TranslationConfig
    dynamic_intent: DynamicIntentConfig
    quality_ranking: QualityRankingConfig
    title_ranking: TitleRankingConfig
    intent_ranking: IntentRankingConfig
    field_relevance: FieldRelevanceConfig
    field_consensus: FieldConsensusConfig
    relevance_contradiction: RelevanceContradictionConfig
    relevance_debug: RelevanceDebugConfig
    pagination: PaginationConfig
    source_fields: SourceFieldsConfig
    ui: UIConfig
    autocomplete_ui: AutocompleteUIConfig
    product_url_template: str


@dataclass(frozen=True)
class IntentRule:
    """`intent_rules.json`daki bir kural — dinamik kategori keşfinin üzerine
    binen opsiyonel, özel-durum override'ı (alias/force-boost/exclusion/
    display-label/icon/priority/enabled)."""

    name: str
    query_terms: tuple[str, ...]  # legacy aliases / tetikleyici terimler (any-match)
    excluded_when_query_contains: tuple[str, ...]
    category_boost_terms: tuple[str, ...]  # force_boost terimleri (legacy)
    negative_categories: tuple[str, ...]  # legacy hard exclusion değerleri
    category_boost: float
    category_boost_tr: float
    label: str  # display_label
    icon: str
    priority: float = 0
    enabled: bool = True
    all_terms: tuple[str, ...] = ()  # hepsi eşleşmeli (AND)
    any_terms: tuple[str, ...] = ()  # `query_terms`in yeni adı; boşsa query_terms kullanılır (OR)
    excluded_terms: tuple[str, ...] = ()  # `excluded_when_query_contains`in yeni adı
    positive_categories: tuple[CategoryBoostEntry, ...] = ()  # yeni: obje-biçimli boost
    soft_negative_categories: tuple[CategoryPenaltyEntry, ...] = ()  # yeni: obje-biçimli penalty
    contradiction_terms: tuple[str, ...] = ()  # yeni: serbest metin çelişki terimleri (bkz. spec §5)


@dataclass(frozen=True)
class TranslationDictionary:
    phrases: dict[str, tuple[str, ...]] = field(default_factory=dict)
    terms: dict[str, tuple[str, ...]] = field(default_factory=dict)


# ============================================================
# YÜKLEYİCİLER
# ============================================================

def _build_field_boosts(raw: Any, context: str) -> tuple[FieldBoost, ...]:
    raw = _require_dict(raw, context)
    if not raw:
        raise ConfigError(f"{context} en az bir alan içermeli.")
    boosts = []
    for field_name, boost_value in raw.items():
        boost = _require_positive_number(boost_value, f"{context}.{field_name} boost")
        boosts.append(FieldBoost(field=field_name, boost=boost))
    return tuple(boosts)


def _build_dynamic_intent(raw: Any, context: str) -> DynamicIntentConfig:
    raw = _require_dict(raw, context)
    aggregation_fields = _require_str_list(raw.get("aggregation_fields"), f"{context}.aggregation_fields")
    if len(set(aggregation_fields)) != len(aggregation_fields):
        raise ConfigError(f"{context}.aggregation_fields tekrarlı alan içeremez.")

    return DynamicIntentConfig(
        enabled=_require_bool(raw.get("enabled"), f"{context}.enabled"),
        cache_ttl_seconds=_require_positive_int(raw.get("cache_ttl_seconds"), f"{context}.cache_ttl_seconds"),
        max_category_candidates=_require_positive_int(
            raw.get("max_category_candidates"), f"{context}.max_category_candidates"
        ),
        minimum_query_length=_require_positive_int(
            raw.get("minimum_query_length"), f"{context}.minimum_query_length"
        ),
        aggregation_size=_require_positive_int(raw.get("aggregation_size"), f"{context}.aggregation_size"),
        boost=_require_positive_number(raw.get("boost"), f"{context}.boost"),
        timeout_seconds=_require_positive_int(raw.get("timeout_seconds"), f"{context}.timeout_seconds"),
        search_fields=_build_field_boosts(raw.get("search_fields"), f"{context}.search_fields"),
        aggregation_fields=aggregation_fields,
    )


def _build_field_relevance(raw: Any, context: str) -> FieldRelevanceConfig:
    raw = _require_dict(raw, context)
    return FieldRelevanceConfig(
        enabled=_require_bool(raw.get("enabled"), f"{context}.enabled"),
        operator=_require_str(raw.get("operator"), f"{context}.operator"),
        fields=_build_field_boosts(raw.get("fields"), f"{context}.fields"),
        cross_fields_boost=_require_positive_number(raw.get("cross_fields_boost"), f"{context}.cross_fields_boost"),
        store_boost=_require_positive_number(raw.get("store_boost"), f"{context}.store_boost", allow_zero=True),
    )


def _build_field_consensus(raw: Any, context: str) -> FieldConsensusConfig:
    raw = _require_dict(raw, context)
    two = _require_positive_number(raw.get("two_field_boost"), f"{context}.two_field_boost")
    three = _require_positive_number(raw.get("three_field_boost"), f"{context}.three_field_boost")
    four_plus = _require_positive_number(raw.get("four_plus_field_boost"), f"{context}.four_plus_field_boost")
    if not (two <= three <= four_plus):
        raise ConfigError(
            f"{context}: two_field_boost <= three_field_boost <= four_plus_field_boost olmalı "
            f"(alınan: {two}, {three}, {four_plus})."
        )
    return FieldConsensusConfig(
        enabled=_require_bool(raw.get("enabled"), f"{context}.enabled"),
        two_field_boost=two,
        three_field_boost=three,
        four_plus_field_boost=four_plus,
        counted_fields=tuple(_require_str_list(raw.get("counted_fields"), f"{context}.counted_fields")),
    )


def _build_relevance_contradiction(raw: Any, context: str) -> RelevanceContradictionConfig:
    raw = _require_dict(raw, context)
    minimum_fields = _require_positive_int(raw.get("minimum_conflicting_fields"), f"{context}.minimum_conflicting_fields")
    strong_fields = _require_positive_int(raw.get("strong_conflicting_fields"), f"{context}.strong_conflicting_fields")
    if strong_fields <= minimum_fields:
        raise ConfigError(f"{context}.strong_conflicting_fields, minimum_conflicting_fields'tan büyük olmalı.")

    mild_penalty = _require_positive_number(raw.get("mild_penalty"), f"{context}.mild_penalty")
    strong_penalty = _require_positive_number(raw.get("strong_penalty"), f"{context}.strong_penalty")
    minimum_penalty = _require_positive_number(raw.get("minimum_penalty"), f"{context}.minimum_penalty")
    for name, value in (("mild_penalty", mild_penalty), ("strong_penalty", strong_penalty), ("minimum_penalty", minimum_penalty)):
        if value > 1:
            raise ConfigError(f"{context}.{name} 0-1 aralığında olmalı.")
    if not (minimum_penalty <= strong_penalty < mild_penalty):
        raise ConfigError(
            f"{context}: minimum_penalty <= strong_penalty < mild_penalty olmalı "
            f"(alınan: {minimum_penalty}, {strong_penalty}, {mild_penalty})."
        )

    return RelevanceContradictionConfig(
        enabled=_require_bool(raw.get("enabled"), f"{context}.enabled"),
        minimum_conflicting_fields=minimum_fields,
        strong_conflicting_fields=strong_fields,
        mild_penalty=mild_penalty,
        strong_penalty=strong_penalty,
        minimum_penalty=minimum_penalty,
        counted_fields=tuple(_require_str_list(raw.get("counted_fields"), f"{context}.counted_fields")),
    )


def _build_relevance_debug(raw: Any, context: str) -> RelevanceDebugConfig:
    raw = _require_dict(raw, context)
    return RelevanceDebugConfig(
        explain_top_n=_require_positive_int(raw.get("explain_top_n"), f"{context}.explain_top_n"),
    )


_VALID_MISSING_VALUE_BEHAVIORS = ("neutral", "skip")


def _build_quality_ranking(raw: Any, context: str) -> QualityRankingConfig:
    raw = _require_dict(raw, context)

    missing_value_behavior = _require_str(
        raw.get("missing_value_behavior"), f"{context}.missing_value_behavior"
    )
    if missing_value_behavior not in _VALID_MISSING_VALUE_BEHAVIORS:
        raise ConfigError(
            f"{context}.missing_value_behavior {_VALID_MISSING_VALUE_BEHAVIORS} içinden biri olmalı, "
            f"alınan: {missing_value_behavior!r}"
        )

    low_consistency_threshold = _require_positive_number(
        raw.get("low_consistency_threshold"), f"{context}.low_consistency_threshold"
    )
    if low_consistency_threshold > 1:
        raise ConfigError(f"{context}.low_consistency_threshold 0-1 aralığında olmalı.")

    discovery_min_data_quality_score = _require_positive_number(
        raw.get("discovery_min_data_quality_score"), f"{context}.discovery_min_data_quality_score"
    )
    if discovery_min_data_quality_score > 1:
        raise ConfigError(f"{context}.discovery_min_data_quality_score 0-1 aralığında olmalı.")

    return QualityRankingConfig(
        enabled=_require_bool(raw.get("enabled"), f"{context}.enabled"),
        score_field=_require_str(raw.get("score_field"), f"{context}.score_field"),
        consistency_field=_require_str(raw.get("consistency_field"), f"{context}.consistency_field"),
        boost=_require_positive_number(raw.get("boost"), f"{context}.boost"),
        consistency_boost=_require_positive_number(raw.get("consistency_boost"), f"{context}.consistency_boost"),
        low_consistency_threshold=low_consistency_threshold,
        low_consistency_penalty=_require_positive_number(
            raw.get("low_consistency_penalty"), f"{context}.low_consistency_penalty"
        ),
        bypass_for_exact_asin=_require_bool(
            raw.get("bypass_for_exact_asin"), f"{context}.bypass_for_exact_asin"
        ),
        missing_value_behavior=missing_value_behavior,
        discovery_filter_enabled=_require_bool(
            raw.get("discovery_filter_enabled"), f"{context}.discovery_filter_enabled"
        ),
        discovery_min_data_quality_score=discovery_min_data_quality_score,
    )


def _build_pagination(raw: Any, context: str) -> PaginationConfig:
    raw = _require_dict(raw, context)
    page_size = _require_positive_int(raw.get("page_size"), f"{context}.page_size")
    max_result_window = _require_positive_int(
        raw.get("max_result_window"), f"{context}.max_result_window"
    )
    if max_result_window < page_size:
        raise ConfigError(
            f"{context}.max_result_window ({max_result_window}), "
            f"page_size'dan ({page_size}) küçük olamaz."
        )
    return PaginationConfig(
        enabled=_require_bool(raw.get("enabled"), f"{context}.enabled"),
        page_size=page_size,
        max_result_window=max_result_window,
        max_visible_pages=_require_positive_int(
            raw.get("max_visible_pages"), f"{context}.max_visible_pages"
        ),
    )


def _build_autocomplete_ui(raw: Any, context: str) -> AutocompleteUIConfig:
    raw = _require_dict(raw, context)
    return AutocompleteUIConfig(
        panel_max_height_px=_require_positive_int(
            raw.get("panel_max_height_px"), f"{context}.panel_max_height_px"
        ),
        row_height_px=_require_positive_int(raw.get("row_height_px"), f"{context}.row_height_px"),
        show_images=_require_bool(raw.get("show_images"), f"{context}.show_images"),
    )


def _build_title_ranking(raw: Any, context: str) -> TitleRankingConfig:
    raw = _require_dict(raw, context)
    return TitleRankingConfig(
        enabled=_require_bool(raw.get("enabled"), f"{context}.enabled"),
        exact_field=_require_str(raw.get("exact_field"), f"{context}.exact_field"),
        exact_boost=_require_positive_number(raw.get("exact_boost"), f"{context}.exact_boost"),
        prefix_boost=_require_positive_number(raw.get("prefix_boost"), f"{context}.prefix_boost"),
        prefix_max_expansions=_require_positive_int(
            raw.get("prefix_max_expansions"), f"{context}.prefix_max_expansions"
        ),
    )


def _build_intent_ranking(raw: Any, context: str) -> IntentRankingConfig:
    raw = _require_dict(raw, context)
    negative_penalty_floor = _require_positive_number(
        raw.get("negative_penalty_floor"), f"{context}.negative_penalty_floor"
    )
    if negative_penalty_floor > 1:
        raise ConfigError(f"{context}.negative_penalty_floor 0-1 aralığında olmalı.")
    return IntentRankingConfig(
        enabled=_require_bool(raw.get("enabled"), f"{context}.enabled"),
        manual_category_boost_cap=_require_positive_number(
            raw.get("manual_category_boost_cap"), f"{context}.manual_category_boost_cap"
        ),
        dynamic_category_boost_cap=_require_positive_number(
            raw.get("dynamic_category_boost_cap"), f"{context}.dynamic_category_boost_cap"
        ),
        negative_penalty_floor=negative_penalty_floor,
    )


def _build_fuzzy(raw: Any, context: str) -> MultiFieldQueryConfig:
    raw = _require_dict(raw, context)
    return MultiFieldQueryConfig(
        type=_require_str(raw.get("type"), f"{context}.type"),
        boost=_require_positive_number(raw.get("boost"), f"{context}.boost"),
        fields=_build_field_boosts(raw.get("fields"), f"{context}.fields"),
        fuzziness=_require_str(raw.get("fuzziness"), f"{context}.fuzziness"),
        prefix_length=_require_positive_int(raw.get("prefix_length"), f"{context}.prefix_length"),
        max_expansions=_require_positive_int(raw.get("max_expansions"), f"{context}.max_expansions"),
    )


@lru_cache(maxsize=None)
def load_search_config(path: Path = SEARCH_CONFIG_PATH) -> AppConfig:
    """`search_config.json`ı okur, doğrular ve `AppConfig` döner. Ortam
    değişkenleri sayısal limitleri geçersiz kılabilir (env > JSON > varsayılan).
    """
    raw = _load_json(path)
    if not raw:
        raise ConfigError(f"{path} bulunamadı veya boş. Arama yapılandırması zorunludur.")

    es_raw = _require_dict(raw.get("elasticsearch"), "elasticsearch")
    elasticsearch = ElasticsearchConfig(
        search_indices=_require_str_list(es_raw.get("search_indices"), "elasticsearch.search_indices"),
        autocomplete_indices=_require_str_list(
            es_raw.get("autocomplete_indices"), "elasticsearch.autocomplete_indices"
        ),
        search_timeout_seconds=_require_positive_int(
            es_raw.get("search_timeout_seconds"), "elasticsearch.search_timeout_seconds"
        ),
        autocomplete_timeout_seconds=_require_positive_int(
            es_raw.get("autocomplete_timeout_seconds"), "elasticsearch.autocomplete_timeout_seconds"
        ),
        track_total_hits=_require_bool(es_raw.get("track_total_hits"), "elasticsearch.track_total_hits"),
        use_dfs_query_then_fetch=_require_bool(
            es_raw.get("use_dfs_query_then_fetch"), "elasticsearch.use_dfs_query_then_fetch"
        ),
    )

    limits_raw = _require_dict(raw.get("limits"), "limits")
    limits = LimitsConfig(
        result_size=_env_int_override(
            "AMAZON_SEARCH_RESULT_SIZE",
            _require_positive_int(limits_raw.get("result_size"), "limits.result_size"),
        ),
        autocomplete_fetch_size=_require_positive_int(
            limits_raw.get("autocomplete_fetch_size"), "limits.autocomplete_fetch_size"
        ),
        autocomplete_display_size=_require_positive_int(
            limits_raw.get("autocomplete_display_size"), "limits.autocomplete_display_size"
        ),
        autocomplete_min_chars=_env_int_override(
            "AMAZON_AUTOCOMPLETE_MIN_CHARS",
            _require_positive_int(limits_raw.get("autocomplete_min_chars"), "limits.autocomplete_min_chars"),
        ),
        autocomplete_cache_ttl_seconds=_require_positive_int(
            limits_raw.get("autocomplete_cache_ttl_seconds"), "limits.autocomplete_cache_ttl_seconds"
        ),
    )

    methods_raw = _require_dict(raw.get("search_methods"), "search_methods")

    exact_asin_raw = _require_dict(methods_raw.get("exact_asin"), "search_methods.exact_asin")
    exact_asin = FieldBoost(
        field=_require_str(exact_asin_raw.get("field"), "search_methods.exact_asin.field"),
        boost=_require_positive_number(exact_asin_raw.get("boost"), "search_methods.exact_asin.boost"),
    )

    phrase_raw = _require_dict(methods_raw.get("phrase"), "search_methods.phrase")
    phrase = FieldBoost(
        field=_require_str(phrase_raw.get("field"), "search_methods.phrase.field"),
        boost=_require_positive_number(phrase_raw.get("boost"), "search_methods.phrase.boost"),
    )

    fuzzy = _build_fuzzy(methods_raw.get("fuzzy"), "search_methods.fuzzy")

    autocomplete_raw = _require_dict(methods_raw.get("autocomplete"), "search_methods.autocomplete")
    autocomplete = AutocompleteQueryConfig(
        field=_require_str(autocomplete_raw.get("field"), "search_methods.autocomplete.field"),
        operator=_require_str(autocomplete_raw.get("operator"), "search_methods.autocomplete.operator"),
    )

    search_methods = SearchMethodsConfig(
        exact_asin=exact_asin,
        phrase=phrase,
        fuzzy=fuzzy,
        autocomplete=autocomplete,
    )

    translation_raw = _require_dict(raw.get("translation"), "translation")
    translation = TranslationConfig(
        enabled=_require_bool(translation_raw.get("enabled"), "translation.enabled"),
        autocomplete_enabled=_require_bool(
            translation_raw.get("autocomplete_enabled"), "translation.autocomplete_enabled"
        ),
        source_language=_require_str(translation_raw.get("source_language"), "translation.source_language"),
        target_language=_require_str(translation_raw.get("target_language"), "translation.target_language"),
        max_variants=_require_positive_int(translation_raw.get("max_variants"), "translation.max_variants"),
        phrase_boost=_require_positive_number(translation_raw.get("phrase_boost"), "translation.phrase_boost"),
        token_boost=_require_positive_number(translation_raw.get("token_boost"), "translation.token_boost"),
        min_query_length=_require_positive_int(
            translation_raw.get("min_query_length"), "translation.min_query_length"
        ),
        cache_ttl_seconds=_require_positive_int(
            translation_raw.get("cache_ttl_seconds"), "translation.cache_ttl_seconds"
        ),
        file=_require_str(translation_raw.get("file"), "translation.file"),
    )

    dynamic_intent = _build_dynamic_intent(raw.get("dynamic_intent"), "dynamic_intent")
    quality_ranking = _build_quality_ranking(raw.get("quality_ranking"), "quality_ranking")
    title_ranking = _build_title_ranking(raw.get("title_ranking"), "title_ranking")
    intent_ranking = _build_intent_ranking(raw.get("intent_ranking"), "intent_ranking")
    field_relevance = _build_field_relevance(raw.get("field_relevance"), "field_relevance")
    field_consensus = _build_field_consensus(raw.get("field_consensus"), "field_consensus")
    relevance_contradiction = _build_relevance_contradiction(raw.get("relevance_contradiction"), "relevance_contradiction")
    relevance_debug = _build_relevance_debug(raw.get("relevance_debug"), "relevance_debug")
    pagination = _build_pagination(raw.get("pagination"), "pagination")

    source_fields_raw = _require_dict(raw.get("source_fields"), "source_fields")
    source_fields = SourceFieldsConfig(
        search=_require_str_list(source_fields_raw.get("search"), "source_fields.search"),
        suggestions=_require_str_list(source_fields_raw.get("suggestions"), "source_fields.suggestions"),
    )

    ui_raw = _require_dict(raw.get("ui"), "ui")
    ui = UIConfig(
        hero_logo=_require_str(ui_raw.get("hero_logo"), "ui.hero_logo"),
        hero_title=_require_str(ui_raw.get("hero_title"), "ui.hero_title"),
        hero_subtitle=_require_str(ui_raw.get("hero_subtitle"), "ui.hero_subtitle"),
        search_placeholder=_require_str(ui_raw.get("search_placeholder"), "ui.search_placeholder"),
        placeholder_image=_require_str(ui_raw.get("placeholder_image"), "ui.placeholder_image"),
        example_queries=_require_str_list(ui_raw.get("example_queries"), "ui.example_queries"),
        debounce_ms=_require_positive_int(ui_raw.get("debounce_ms"), "ui.debounce_ms"),
        intent_fallback_icon=_require_str(ui_raw.get("intent_fallback_icon"), "ui.intent_fallback_icon"),
        labels=_require_dict(ui_raw.get("labels", {}), "ui.labels"),
        help_text=_require_dict(ui_raw.get("help_text", {}), "ui.help_text"),
        messages=_require_dict(ui_raw.get("messages", {}), "ui.messages"),
    )

    autocomplete_ui = _build_autocomplete_ui(raw.get("autocomplete_ui"), "autocomplete_ui")

    product_url_template = _require_str(raw.get("product_url_template"), "product_url_template")
    if "{asin}" not in product_url_template:
        raise ConfigError("product_url_template içinde '{asin}' yer tutucusu bulunmalı.")

    return AppConfig(
        elasticsearch=elasticsearch,
        limits=limits,
        search_methods=search_methods,
        translation=translation,
        dynamic_intent=dynamic_intent,
        quality_ranking=quality_ranking,
        title_ranking=title_ranking,
        intent_ranking=intent_ranking,
        field_relevance=field_relevance,
        field_consensus=field_consensus,
        relevance_contradiction=relevance_contradiction,
        relevance_debug=relevance_debug,
        pagination=pagination,
        source_fields=source_fields,
        ui=ui,
        autocomplete_ui=autocomplete_ui,
        product_url_template=product_url_template,
    )


def _build_category_boost_entries(raw: Any, context: str) -> tuple["CategoryBoostEntry", ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ConfigError(f"{context} bir liste olmalı, alınan: {raw!r}")
    entries = []
    for index, item in enumerate(raw):
        item_context = f"{context}[{index}]"
        item = _require_dict(item, item_context)
        entries.append(CategoryBoostEntry(
            value=_require_str(item.get("value"), f"{item_context}.value"),
            boost=_require_positive_number(item.get("boost"), f"{item_context}.boost"),
        ))
    return tuple(entries)


def _build_negative_categories(
    raw: Any, context: str
) -> tuple[tuple[str, ...], tuple["CategoryPenaltyEntry", ...]]:
    """Şekle göre ayrıştırır: tümü string ise legacy hard-exclusion listesi,
    tümü {'value','penalty'} nesnesi ise yeni soft-penalty listesi döner.
    Karışık şekil (bir kuralın aynı listesinde hem string hem nesne)
    reddedilir — hard/soft ayrımı asla belirsiz olamaz."""
    if raw is None:
        return (), ()
    if not isinstance(raw, list):
        raise ConfigError(f"{context} bir liste olmalı, alınan: {raw!r}")
    if not raw:
        return (), ()

    is_string_shape = all(isinstance(item, str) for item in raw)
    is_object_shape = all(isinstance(item, dict) for item in raw)
    if not is_string_shape and not is_object_shape:
        raise ConfigError(
            f"{context} ya tümü metin (legacy hard exclusion) ya da tümü "
            f"{{'value','penalty'}} nesnesi (yeni soft penalty) olmalı; karışık olamaz."
        )

    if is_string_shape:
        return tuple(raw), ()

    entries = []
    for index, item in enumerate(raw):
        item_context = f"{context}[{index}]"
        penalty = _require_positive_number(item.get("penalty"), f"{item_context}.penalty")
        if penalty > 1:
            raise ConfigError(f"{item_context}.penalty 0-1 aralığında olmalı.")
        entries.append(CategoryPenaltyEntry(
            value=_require_str(item.get("value"), f"{item_context}.value"),
            penalty=penalty,
        ))
    return (), tuple(entries)


@lru_cache(maxsize=None)
def load_intent_rules(path: Path = INTENT_RULES_PATH) -> dict[str, IntentRule]:
    """`intent_rules.json`ı okur. Dosya yoksa veya `{}` ise boş sözlük döner —
    bu, üründe hiç manuel intent kuralı olmadan da çalışılabileceği anlamına gelir.
    """
    raw = _load_json(path)
    rules: dict[str, IntentRule] = {}

    for name, rule_raw in raw.items():
        context = f"intent_rules.{name}"
        rule_raw = _require_dict(rule_raw, context)
        enabled = rule_raw.get("enabled", True)
        enabled = _require_bool(enabled, f"{context}.enabled")

        query_terms = _require_str_list(
            rule_raw.get("query_terms", []), f"{context}.query_terms", allow_empty=True
        )
        all_terms = _require_str_list(
            rule_raw.get("all_terms", []), f"{context}.all_terms", allow_empty=True
        )
        any_terms = _require_str_list(
            rule_raw.get("any_terms", []), f"{context}.any_terms", allow_empty=True
        )
        if not (query_terms or all_terms or any_terms):
            raise ConfigError(
                f"{context} en az bir tetikleyici terim listesi içermeli "
                f"(query_terms, any_terms veya all_terms)."
            )

        negative_categories, soft_negative_categories = _build_negative_categories(
            rule_raw.get("negative_categories"), f"{context}.negative_categories"
        )

        rules[name] = IntentRule(
            name=name,
            query_terms=query_terms,
            excluded_when_query_contains=_require_str_list(
                rule_raw.get("excluded_when_query_contains", []),
                f"{context}.excluded_when_query_contains",
                allow_empty=True,
            ),
            category_boost_terms=_require_str_list(
                rule_raw.get("category_boost_terms", []),
                f"{context}.category_boost_terms",
                allow_empty=True,
            ),
            negative_categories=negative_categories,
            category_boost=_require_positive_number(
                rule_raw.get("category_boost", 1), f"{context}.category_boost"
            ),
            category_boost_tr=_require_positive_number(
                rule_raw.get("category_boost_tr", 1), f"{context}.category_boost_tr"
            ),
            label=_require_str(rule_raw.get("label", name), f"{context}.label"),
            icon=rule_raw.get("icon", "") or "",
            priority=_require_positive_number(rule_raw.get("priority", 1), f"{context}.priority")
            if "priority" in rule_raw
            else 0,
            enabled=enabled,
            all_terms=all_terms,
            any_terms=any_terms,
            excluded_terms=_require_str_list(
                rule_raw.get("excluded_terms", []), f"{context}.excluded_terms", allow_empty=True
            ),
            positive_categories=_build_category_boost_entries(
                rule_raw.get("positive_categories"), f"{context}.positive_categories"
            ),
            soft_negative_categories=soft_negative_categories,
            contradiction_terms=tuple(
                _require_str_list(
                    rule_raw.get("contradiction_terms", []), f"{context}.contradiction_terms", allow_empty=True
                )
            ),
        )

    return rules


@lru_cache(maxsize=None)
def load_translations(path: Path = TRANSLATIONS_PATH) -> TranslationDictionary:
    """`query_translations.json`ı okur. Dosya yoksa veya boşsa, çeviri
    yapılmadan orijinal sorguyla aramaya devam edilebilmesi için boş bir
    sözlük döner (crash etmez).
    """
    raw = _load_json(path)
    if not raw:
        return TranslationDictionary()

    def _build_section(key: str) -> dict[str, tuple[str, ...]]:
        section_raw = raw.get(key, {})
        section_raw = _require_dict(section_raw, f"query_translations.{key}")
        section: dict[str, tuple[str, ...]] = {}
        for source_text, variants in section_raw.items():
            section[source_text] = _require_str_list(
                variants, f"query_translations.{key}.{source_text}"
            )
        return section

    return TranslationDictionary(
        phrases=_build_section("phrases"),
        terms=_build_section("terms"),
    )


def clear_config_cache() -> None:
    """Test ortamlarında farklı config dosyalarıyla yeniden yükleme yapabilmek için."""
    load_search_config.cache_clear()
    load_intent_rules.cache_clear()
    load_translations.cache_clear()
