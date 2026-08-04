"""
Ürün veri kalitesi / title-category tutarlılığı değerlendirmesi.

Bu modül Elasticsearch'e BAĞLANMAZ; saf, deterministik ve test edilebilir bir
fonksiyondur (bkz. `evaluate_product_quality`). Tek tek ürün türüne özel
hardcoded intent kuralları içermez — genel bir kategori-ailesi taksonomisi
(`config/category_taxonomy.json`) ve ağırlık/eşik yapılandırması
(`config/quality_config.json`) üzerinden çalışır. Yeni bir ürün türü veya
ürün ailesi eklemek için bu iki JSON dosyası güncellenir; kod değişikliği
gerekmez.

Ana giriş noktası:

    evaluate_product_quality(product: dict) -> dict

Dönüş (üretim belgelerinde saklanan alanlar):
    {
      "title_category_consistency": 0.0-1.0,
      "data_quality_score": 0.0-1.0,
      "quality_flags": [...],
      "quality_version": "v1",
    }

`include_explanation=True` (veya config'teki `include_debug_explanation`)
ile ek olarak `quality_explanation` (matched_terms/conflicting_terms/signals)
döner — bu, production `_source`'unda yer kaplamaması için varsayılan olarak
KAPALIDIR; yalnızca importer debug logları veya offline değerlendirme aracı
için açılır (bkz. `evaluate_quality_sample.py`, `inspect_amazon.py --quality`).

Algoritma özeti (bkz. proje CLAUDE.md §3):
  1. title ve category metinleri normalize edilir (Unicode NFKC + casefold).
  2. Her ikisi de taksonomideki 12 ürün ailesine karşı puanlanır (phrase eşleşmesi
     token eşleşmesinden daha ağır sayılır; jenerik terimler hiç puan katmaz).
  3. Her taraf için "baskın aile" yalnızca yeterli sinyal VE ikinci adaya karşı
     yeterli marj varsa belirlenir; aksi halde "yetersiz sinyal" (ambiguous)
     kabul edilir — bu, tek kelimelik zayıf/çok-anlamlı sinyallerden
     (ör. bağlamsız "watch", "light", "chair") yanlış pozitif üretilmesini
     engeller (bkz. §4 false-positive korumaları).
  4. Aileler eşitse yüksek tutarlılık; aileler farklı ve konfigürasyondaki
     `conflicting_families` listesinde birbirine karşıtsa düşük tutarlılık
     (mismatch flag'i eklenir); farklı ama karşıt olarak tanımlı değilse
     (çok-amaçlı/kesişen ürünler) orta seviye, flag'siz bir skor verilir —
     tamamen eleme değil, skor düşürme yaklaşımı.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

QUALITY_VERSION = "v1"

BASE_DIR = Path(__file__).parent
CONFIG_DIR = BASE_DIR / "config"
TAXONOMY_PATH = CONFIG_DIR / "category_taxonomy.json"
QUALITY_CONFIG_PATH = CONFIG_DIR / "quality_config.json"

_NON_WORD_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WHITESPACE_RE = re.compile(r"\s+")


class QualityConfigError(Exception):
    """Kalite yapılandırma dosyaları (taxonomy/quality_config) okunamadığında
    veya geçersiz olduğunda fırlatılır."""


# ============================================================
# METİN NORMALİZASYONU
# ============================================================

def normalize_text(text: Any) -> str:
    """
    Unicode NFKC normalize + casefold + noktalama temizliği.

    Türkçe'ye özgü büyük/küçük harf dönüşümü (İ/I/ı) BİLEREK uygulanmaz —
    bu fonksiyon İngilizce ürün title/category metinleri üzerinde çalışır;
    Türkçe-özel casing marka/model kodlarını (ör. "IP68") bozabilir. Yalnızca
    dil-bağımsız Unicode casefold kullanılır (bkz. CLAUDE.md §11: brand/model/
    ASIN korunmalı).
    """
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKC", str(text))
    normalized = normalized.casefold()
    normalized = normalized.replace("_", " ").replace("-", " ")
    normalized = _NON_WORD_RE.sub(" ", normalized)
    normalized = _WHITESPACE_RE.sub(" ", normalized).strip()
    return normalized


def _is_code_like(token: str, min_length: int) -> bool:
    """Model kodu / ASIN benzeri token'ları (harf+rakam karışık) tespit eder —
    bunlar kategori-aile sinyali taşımaz, "aşırı jenerik başlık" tespitinde
    yanlışlıkla jenerik sayılmamaları için ayrı tutulur."""
    if len(token) < min_length:
        return False
    has_alpha = any(c.isalpha() for c in token)
    has_digit = any(c.isdigit() for c in token)
    return has_alpha and has_digit


# ============================================================
# VERİ SINIFLARI
# ============================================================

@dataclass(frozen=True)
class CategoryFamily:
    name: str
    label: str
    # (normalized_term, is_phrase) — önceden normalize edilmiş, tekrar hesap gerekmez.
    title_match_terms: tuple[tuple[str, bool], ...]
    category_match_terms: tuple[tuple[str, bool], ...]
    conflicting_families: frozenset[str]


@dataclass(frozen=True)
class Taxonomy:
    families: dict[str, CategoryFamily]
    generic_terms: frozenset[str]


@dataclass(frozen=True)
class QualityConfig:
    quality_version: str
    include_debug_explanation: bool

    phrase_match_weight: float
    token_match_weight: float
    min_family_signal: float
    dominance_margin_ratio: float
    family_match_score: float
    insufficient_signal_score: float
    cross_family_soft_score: float
    conflict_score: float
    overlap_blend_weight: float

    min_meaningful_tokens: int
    code_like_min_length: int

    dq_weight_consistency: float
    dq_weight_completeness: float
    dq_weight_validity: float

    completeness_has_title: float
    completeness_has_categories: float
    completeness_has_description_or_features: float
    completeness_has_image: float
    completeness_has_asin: float
    completeness_not_generic_title: float

    validity_rating_weight: float
    validity_rating_number_weight: float
    validity_price_weight: float

    stopwords: frozenset[str]

    flag_mismatch: str
    flag_missing_title: str
    flag_missing_categories: str
    flag_missing_title_and_categories: str
    flag_generic_title: str
    flag_missing_description_and_features: str
    flag_missing_image: str
    flag_missing_asin: str
    flag_invalid_rating: str
    flag_invalid_rating_number: str
    flag_invalid_price: str
    flag_evaluation_failed: str


# ============================================================
# YARDIMCI DOĞRULAMA / YÜKLEME
# ============================================================

def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise QualityConfigError(f"{path} bulunamadı.")
    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except json.JSONDecodeError as error:
        raise QualityConfigError(f"{path} geçerli bir JSON dosyası değil: {error}") from error
    if not isinstance(data, dict):
        raise QualityConfigError(f"{path} bir JSON nesnesi (obje) içermeli.")
    return data


def _require_dict(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise QualityConfigError(f"{context} bir JSON nesnesi olmalı, alınan: {type(value).__name__}")
    return value


def _require_str_list(value: Any, context: str, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise QualityConfigError(f"{context} metin listesi olmalı, alınan: {value!r}")
    if not allow_empty and not value:
        raise QualityConfigError(f"{context} en az bir eleman içermeli.")
    return value


def _require_number(value: Any, context: str, allow_negative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise QualityConfigError(f"{context} sayı olmalı, alınan: {value!r}")
    if not allow_negative and value < 0:
        raise QualityConfigError(f"{context} negatif olamaz, alınan: {value!r}")
    return float(value)


def _require_positive_int(value: Any, context: str) -> int:
    number = _require_number(value, context)
    if number != int(number) or number <= 0:
        raise QualityConfigError(f"{context} pozitif tam sayı olmalı, alınan: {value!r}")
    return int(number)


def _require_bool(value: Any, context: str) -> bool:
    if not isinstance(value, bool):
        raise QualityConfigError(f"{context} true/false olmalı, alınan: {value!r}")
    return value


def _require_str(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise QualityConfigError(f"{context} boş olmayan bir metin olmalı, alınan: {value!r}")
    return value


def _build_match_terms(terms: list[str]) -> tuple[tuple[str, bool], ...]:
    result: list[tuple[str, bool]] = []
    seen: set[str] = set()
    for term in terms:
        if not isinstance(term, str) or not term.strip():
            continue
        norm = normalize_text(term)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        result.append((norm, " " in norm))
    return tuple(result)


@lru_cache(maxsize=None)
def load_category_taxonomy(path: Path = TAXONOMY_PATH) -> Taxonomy:
    """`config/category_taxonomy.json`ı okur ve doğrular. Boş/eksik/bozuk
    dosya `QualityConfigError` fırlatır (sessizce boş taksonomi ile devam
    edilmez — bu, kalite sinyalinin farkında olmadan devre dışı kalmasını
    önler)."""
    raw = _load_json(path)

    families_raw = _require_dict(raw.get("families"), "families")
    if not families_raw:
        raise QualityConfigError(f"{path}: 'families' en az bir aile içermeli.")

    generic_terms_raw = raw.get("generic_terms", [])
    if not isinstance(generic_terms_raw, list):
        raise QualityConfigError(f"{path}: 'generic_terms' bir liste olmalı.")
    generic_terms = frozenset(
        normalize_text(term) for term in generic_terms_raw if isinstance(term, str)
    )

    families: dict[str, CategoryFamily] = {}
    for name, body in families_raw.items():
        context = f"families.{name}"
        body = _require_dict(body, context)

        title_terms = _require_str_list(body.get("title_terms", []), f"{context}.title_terms", allow_empty=True)
        category_terms = _require_str_list(
            body.get("category_terms", []), f"{context}.category_terms", allow_empty=True
        )
        aliases = _require_dict(body.get("aliases", {}), f"{context}.aliases")
        conflicting = _require_str_list(
            body.get("conflicting_families", []), f"{context}.conflicting_families", allow_empty=True
        )

        alias_terms = [key for key in aliases.keys() if isinstance(key, str)]

        families[name] = CategoryFamily(
            name=name,
            label=str(body.get("label", name)),
            title_match_terms=_build_match_terms(list(title_terms) + alias_terms),
            category_match_terms=_build_match_terms(list(category_terms) + alias_terms),
            conflicting_families=frozenset(conflicting),
        )

    unknown_refs: dict[str, list[str]] = {}
    for name, fam in families.items():
        unknown = sorted(fam.conflicting_families - families.keys())
        if unknown:
            unknown_refs[name] = unknown
    if unknown_refs:
        raise QualityConfigError(
            f"{path}: conflicting_families bilinmeyen aile(ler) referans ediyor: {unknown_refs}"
        )

    return Taxonomy(families=families, generic_terms=generic_terms)


@lru_cache(maxsize=None)
def load_quality_config(path: Path = QUALITY_CONFIG_PATH) -> QualityConfig:
    """`config/quality_config.json`ı okur ve doğrular."""
    raw = _load_json(path)

    cs = _require_dict(raw.get("consistency_scoring"), "consistency_scoring")
    ta = _require_dict(raw.get("title_analysis"), "title_analysis")
    weights = _require_dict(raw.get("weights"), "weights")
    dq = _require_dict(weights.get("data_quality"), "weights.data_quality")
    comp = _require_dict(raw.get("completeness_signals"), "completeness_signals")
    val = _require_dict(raw.get("validity_signals"), "validity_signals")
    flags = _require_dict(raw.get("flags"), "flags")

    stopwords_raw = raw.get("stopwords", [])
    if not isinstance(stopwords_raw, list):
        raise QualityConfigError("stopwords bir liste olmalı.")
    stopwords = frozenset(normalize_text(w) for w in stopwords_raw if isinstance(w, str))

    dq_consistency = _require_number(dq.get("consistency"), "weights.data_quality.consistency")
    dq_completeness = _require_number(dq.get("completeness"), "weights.data_quality.completeness")
    dq_validity = _require_number(dq.get("validity"), "weights.data_quality.validity")
    if abs((dq_consistency + dq_completeness + dq_validity) - 1.0) > 0.01:
        raise QualityConfigError(
            "weights.data_quality (consistency+completeness+validity) toplamı 1.0 olmalı."
        )

    comp_keys = [
        "has_title", "has_categories", "has_description_or_features",
        "has_image", "has_asin", "not_generic_title",
    ]
    comp_values = {key: _require_number(comp.get(key), f"completeness_signals.{key}") for key in comp_keys}
    if abs(sum(comp_values.values()) - 1.0) > 0.01:
        raise QualityConfigError("completeness_signals ağırlıkları toplamı 1.0 olmalı.")

    val_keys = ["rating_valid_or_absent", "rating_number_valid_or_absent", "price_valid_or_absent"]
    val_values = {key: _require_number(val.get(key), f"validity_signals.{key}") for key in val_keys}
    if abs(sum(val_values.values()) - 1.0) > 0.01:
        raise QualityConfigError("validity_signals ağırlıkları toplamı 1.0 olmalı.")

    return QualityConfig(
        quality_version=_require_str(raw.get("quality_version"), "quality_version"),
        include_debug_explanation=_require_bool(
            raw.get("include_debug_explanation", False), "include_debug_explanation"
        ),
        phrase_match_weight=_require_number(cs.get("phrase_match_weight"), "consistency_scoring.phrase_match_weight"),
        token_match_weight=_require_number(cs.get("token_match_weight"), "consistency_scoring.token_match_weight"),
        min_family_signal=_require_number(cs.get("min_family_signal"), "consistency_scoring.min_family_signal"),
        dominance_margin_ratio=_require_number(
            cs.get("dominance_margin_ratio"), "consistency_scoring.dominance_margin_ratio"
        ),
        family_match_score=_require_number(cs.get("family_match_score"), "consistency_scoring.family_match_score"),
        insufficient_signal_score=_require_number(
            cs.get("insufficient_signal_score"), "consistency_scoring.insufficient_signal_score"
        ),
        cross_family_soft_score=_require_number(
            cs.get("cross_family_soft_score"), "consistency_scoring.cross_family_soft_score"
        ),
        conflict_score=_require_number(cs.get("conflict_score"), "consistency_scoring.conflict_score"),
        overlap_blend_weight=_require_number(
            cs.get("overlap_blend_weight"), "consistency_scoring.overlap_blend_weight"
        ),
        min_meaningful_tokens=_require_positive_int(
            ta.get("min_meaningful_tokens"), "title_analysis.min_meaningful_tokens"
        ),
        code_like_min_length=_require_positive_int(
            ta.get("code_like_min_length"), "title_analysis.code_like_min_length"
        ),
        dq_weight_consistency=dq_consistency,
        dq_weight_completeness=dq_completeness,
        dq_weight_validity=dq_validity,
        completeness_has_title=comp_values["has_title"],
        completeness_has_categories=comp_values["has_categories"],
        completeness_has_description_or_features=comp_values["has_description_or_features"],
        completeness_has_image=comp_values["has_image"],
        completeness_has_asin=comp_values["has_asin"],
        completeness_not_generic_title=comp_values["not_generic_title"],
        validity_rating_weight=val_values["rating_valid_or_absent"],
        validity_rating_number_weight=val_values["rating_number_valid_or_absent"],
        validity_price_weight=val_values["price_valid_or_absent"],
        stopwords=stopwords,
        flag_mismatch=_require_str(flags.get("mismatch"), "flags.mismatch"),
        flag_missing_title=_require_str(flags.get("missing_title"), "flags.missing_title"),
        flag_missing_categories=_require_str(flags.get("missing_categories"), "flags.missing_categories"),
        flag_missing_title_and_categories=_require_str(
            flags.get("missing_title_and_categories"), "flags.missing_title_and_categories"
        ),
        flag_generic_title=_require_str(flags.get("generic_title"), "flags.generic_title"),
        flag_missing_description_and_features=_require_str(
            flags.get("missing_description_and_features"), "flags.missing_description_and_features"
        ),
        flag_missing_image=_require_str(flags.get("missing_image"), "flags.missing_image"),
        flag_missing_asin=_require_str(flags.get("missing_asin"), "flags.missing_asin"),
        flag_invalid_rating=_require_str(flags.get("invalid_rating"), "flags.invalid_rating"),
        flag_invalid_rating_number=_require_str(
            flags.get("invalid_rating_number"), "flags.invalid_rating_number"
        ),
        flag_invalid_price=_require_str(flags.get("invalid_price"), "flags.invalid_price"),
        flag_evaluation_failed=_require_str(flags.get("evaluation_failed"), "flags.evaluation_failed"),
    )


def clear_quality_config_cache() -> None:
    """Testlerde farklı config dosyalarıyla yeniden yükleme yapabilmek için."""
    load_category_taxonomy.cache_clear()
    load_quality_config.cache_clear()


# ============================================================
# METİN / AİLE PUANLAMA
# ============================================================

def _combine_category_text(product: dict[str, Any]) -> str:
    """main_category + categories + categories_text + source_category
    alanlarını tek bir metinde birleştirir. Beklenmeyen tipler (malformed
    categories: dict, int, vb.) sessizce yok sayılır — hata fırlatmaz."""
    parts: list[str] = []

    main_category = product.get("main_category")
    if isinstance(main_category, str):
        parts.append(main_category)

    categories = product.get("categories")
    if isinstance(categories, str):
        parts.append(categories)
    elif isinstance(categories, (list, tuple)):
        parts.extend(item for item in categories if isinstance(item, str))

    categories_text = product.get("categories_text")
    if isinstance(categories_text, str):
        parts.append(categories_text)

    source_category = product.get("source_category")
    if isinstance(source_category, str):
        parts.append(source_category.replace("_", " "))

    return " ".join(part for part in parts if part and part.strip())


def _has_text_content(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple)):
        return any(_has_text_content(item) for item in value)
    return False


def _family_scores(
    text_norm: str,
    taxonomy: Taxonomy,
    side: str,
    cfg: QualityConfig,
) -> dict[str, float]:
    """Normalize edilmiş metni tüm ailelere karşı puanlar. Phrase eşleşmesi
    token eşleşmesinden ağır sayılır; sıfır puanlı aileler sonuca dahil edilmez."""
    if not text_norm:
        return {}

    padded = f" {text_norm} "
    scores: dict[str, float] = {}

    for name, fam in taxonomy.families.items():
        terms = fam.title_match_terms if side == "title" else fam.category_match_terms
        score = 0.0
        for term_norm, is_phrase in terms:
            if f" {term_norm} " in padded:
                score += cfg.phrase_match_weight if is_phrase else cfg.token_match_weight
        if score > 0:
            scores[name] = score

    return scores


def _dominant_family(
    scores: dict[str, float],
    min_signal: float,
    margin_ratio: float,
) -> str | None:
    """Yalnızca yeterli mutlak sinyal (`min_signal`) VE ikinci en yüksek
    adaya karşı yeterli marj (`margin_ratio`) varsa bir aileyi "baskın" kabul
    eder. Aksi halde None (yetersiz/belirsiz sinyal) döner — bu, zayıf tek
    kelimelik eşleşmelerin güvenilmez biçimde bir aile iddia etmesini
    engeller (false-positive koruması)."""
    if not scores:
        return None

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    top_name, top_score = ranked[0]
    if top_score < min_signal or top_score <= 0:
        return None

    second_score = ranked[1][1] if len(ranked) > 1 else 0.0
    margin = (top_score - second_score) / top_score
    if margin < margin_ratio:
        return None

    return top_name


def _meaningful_tokens(text_norm: str, generic_terms: frozenset[str], cfg: QualityConfig) -> set[str]:
    """Jenerik/stopword/kod-benzeri (marka/model/ASIN) token'ları çıkarır —
    kalan, ürünü gerçekten tanımlayan token kümesidir. "Aşırı jenerik başlık"
    tespiti ve token overlap sinyali için kullanılır."""
    meaningful: set[str] = set()
    for token in text_norm.split():
        if len(token) < 2:
            continue
        if token in generic_terms or token in cfg.stopwords:
            continue
        if _is_code_like(token, cfg.code_like_min_length):
            continue
        meaningful.add(token)
    return meaningful


def _token_overlap_ratio(title_tokens: set[str], category_tokens: set[str]) -> float:
    if not title_tokens:
        return 0.0
    overlap = title_tokens & category_tokens
    return len(overlap) / len(title_tokens)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _validate_optional_range(value: Any, minimum: float, maximum: float | None) -> bool | None:
    """None -> veri yok (nötr, hata sayılmaz). Geçersiz tip/NaN/aralık dışı -> False.
    Geçerli -> True. Düşük değer (ör. rating=1.0, price=0.5) asla False değildir —
    yalnızca eksik/bozuk veri ölçülür, popülerlik/fiyat düzeyi değil."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    if number != number:  # NaN
        return False
    if number < minimum:
        return False
    if maximum is not None and number > maximum:
        return False
    return True


def _validate_rating_number(value: Any) -> bool | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    if number != number or number < 0:
        return False
    return number == int(number)


# ============================================================
# ANA GİRİŞ NOKTASI
# ============================================================

def evaluate_product_quality(
    product: dict[str, Any],
    *,
    taxonomy: Taxonomy | None = None,
    quality_config: QualityConfig | None = None,
    include_explanation: bool | None = None,
) -> dict[str, Any]:
    """
    Bir ürün belgesinin title-category tutarlılığını ve genel veri kalitesini
    değerlendirir. Elasticsearch'e istek ATMAZ; saf ve deterministiktir.

    Herhangi bir hata (bozuk config, beklenmeyen veri tipi vb.) durumunda
    importer'ı veya arama akışını ASLA kesmeyen güvenli bir fallback döner
    (bkz. CLAUDE.md §5 IMPORTER ENTEGRASYONU): data_quality_score=0.5,
    title_category_consistency=0.5, quality_flags=["quality_evaluation_failed"].
    """
    try:
        active_taxonomy = taxonomy if taxonomy is not None else load_category_taxonomy()
        active_config = quality_config if quality_config is not None else load_quality_config()
        return _evaluate(product if isinstance(product, dict) else {}, active_taxonomy, active_config, include_explanation)
    except Exception:
        return _fallback_quality_result()


def _fallback_quality_result() -> dict[str, Any]:
    return {
        "title_category_consistency": 0.5,
        "data_quality_score": 0.5,
        "quality_flags": ["quality_evaluation_failed"],
        "quality_version": QUALITY_VERSION,
    }


def _evaluate(
    product: dict[str, Any],
    taxonomy: Taxonomy,
    cfg: QualityConfig,
    include_explanation: bool | None,
) -> dict[str, Any]:
    title = product.get("title")
    title = title if isinstance(title, str) else ""
    title_norm = normalize_text(title)

    category_text = _combine_category_text(product)
    category_norm = normalize_text(category_text)

    title_scores = _family_scores(title_norm, taxonomy, "title", cfg)
    category_scores = _family_scores(category_norm, taxonomy, "category", cfg)

    title_family = _dominant_family(title_scores, cfg.min_family_signal, cfg.dominance_margin_ratio)
    category_family = _dominant_family(category_scores, cfg.min_family_signal, cfg.dominance_margin_ratio)

    title_tokens = _meaningful_tokens(title_norm, taxonomy.generic_terms, cfg)
    category_tokens = set(category_norm.split())
    overlap_ratio = _token_overlap_ratio(title_tokens, category_tokens)

    is_mismatch = (
        title_family is not None
        and category_family is not None
        and title_family != category_family
        and category_family in taxonomy.families[title_family].conflicting_families
    )

    if title_family is None or category_family is None:
        base_consistency = cfg.insufficient_signal_score
    elif title_family == category_family:
        base_consistency = cfg.family_match_score
    elif is_mismatch:
        base_consistency = cfg.conflict_score
    else:
        base_consistency = cfg.cross_family_soft_score

    consistency = _clamp01(
        (1 - cfg.overlap_blend_weight) * base_consistency + cfg.overlap_blend_weight * overlap_ratio
    )

    flags: list[str] = []
    if is_mismatch:
        flags.append(cfg.flag_mismatch)

    has_title = bool(title_norm)
    has_categories = bool(category_norm)
    if not has_title and not has_categories:
        flags.append(cfg.flag_missing_title_and_categories)
    else:
        if not has_title:
            flags.append(cfg.flag_missing_title)
        if not has_categories:
            flags.append(cfg.flag_missing_categories)

    is_generic_title = has_title and len(title_tokens) < cfg.min_meaningful_tokens
    if is_generic_title:
        flags.append(cfg.flag_generic_title)

    has_description_or_features = _has_text_content(product.get("description")) or _has_text_content(
        product.get("features")
    )
    if not has_description_or_features:
        flags.append(cfg.flag_missing_description_and_features)

    image_url = product.get("image_url")
    has_image = isinstance(image_url, str) and bool(image_url.strip())
    if not has_image:
        flags.append(cfg.flag_missing_image)

    parent_asin = product.get("parent_asin")
    has_asin = isinstance(parent_asin, str) and bool(parent_asin.strip())
    if not has_asin:
        flags.append(cfg.flag_missing_asin)

    rating_valid = _validate_optional_range(product.get("average_rating"), 0.0, 5.0)
    if rating_valid is False:
        flags.append(cfg.flag_invalid_rating)

    rating_number_valid = _validate_rating_number(product.get("rating_number"))
    if rating_number_valid is False:
        flags.append(cfg.flag_invalid_rating_number)

    price_valid = _validate_optional_range(product.get("price"), 0.0, None)
    if price_valid is False:
        flags.append(cfg.flag_invalid_price)

    completeness = (
        (cfg.completeness_has_title if has_title else 0.0)
        + (cfg.completeness_has_categories if has_categories else 0.0)
        + (cfg.completeness_has_description_or_features if has_description_or_features else 0.0)
        + (cfg.completeness_has_image if has_image else 0.0)
        + (cfg.completeness_has_asin if has_asin else 0.0)
        + (cfg.completeness_not_generic_title if not is_generic_title else 0.0)
    )

    validity = (
        (cfg.validity_rating_weight if rating_valid is not False else 0.0)
        + (cfg.validity_rating_number_weight if rating_number_valid is not False else 0.0)
        + (cfg.validity_price_weight if price_valid is not False else 0.0)
    )

    data_quality_score = _clamp01(
        cfg.dq_weight_consistency * consistency
        + cfg.dq_weight_completeness * completeness
        + cfg.dq_weight_validity * validity
    )

    result: dict[str, Any] = {
        "title_category_consistency": round(consistency, 4),
        "data_quality_score": round(data_quality_score, 4),
        "quality_flags": flags,
        "quality_version": cfg.quality_version,
    }

    want_explanation = include_explanation if include_explanation is not None else cfg.include_debug_explanation
    if want_explanation:
        matched_terms = []
        conflicting_terms = []
        if title_family and category_family:
            if title_family == category_family:
                matched_terms = [title_family]
            elif is_mismatch:
                conflicting_terms = [title_family, category_family]

        result["quality_explanation"] = {
            "matched_terms": matched_terms,
            "conflicting_terms": conflicting_terms,
            "signals": {
                "title_family": title_family,
                "category_family": category_family,
                "title_family_scores": title_scores,
                "category_family_scores": category_scores,
                "overlap_ratio": round(overlap_ratio, 4),
                "has_title": has_title,
                "has_categories": has_categories,
                "has_description_or_features": has_description_or_features,
                "has_image": has_image,
                "has_asin": has_asin,
                "rating_valid": rating_valid,
                "rating_number_valid": rating_number_valid,
                "price_valid": price_valid,
            },
        }

    return result
