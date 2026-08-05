"""
Canlı öneri (autocomplete) servis katmanı: Edge NGram autocomplete indexi
üzerinden sorgu oluşturma/çalıştırma ve öneri listesi üretme.

`search_service` modülüne MODÜL REFERANSIYLA bağımlıdır
(`search_service.CONFIG`, `search_service._post_search(...)`, ...) —
`from services.search_service import X` KULLANILMAZ, çünkü bu tür bir
isim-kopyalama testlerin `monkeypatch.setattr(search_service, "X", ...)` ile
yaptığı değişiklikleri bu modülden görünmez kılar (import zamanında alınan
kopya, kaynak modül sonradan değişse bile eskisi gibi kalır).

Bu modül de Streamlit'e bağımlı DEĞİLDİR; önbellekleme `app.py`'de yapılır
(bkz. `search_service` docstring'i — aynı DI deseni burada da geçerlidir).
"""

from __future__ import annotations

from services import search_service
from services.search_models import SuggestionItem

__all__ = [
    "AUTOCOMPLETE_INDEX_NAME",
    "SUGGESTION_SOURCE_FIELDS",
    "build_autocomplete_query",
    "fetch_suggestion_hits",
    "get_suggestions",
]

CONFIG = search_service.CONFIG
AUTOCOMPLETE_INDEX_NAME = CONFIG.elasticsearch.autocomplete_index_expr if CONFIG else ""
SUGGESTION_SOURCE_FIELDS = list(CONFIG.source_fields.suggestions) if CONFIG else []


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
    config = search_service.CONFIG
    result_size = result_size if result_size is not None else config.limits.autocomplete_fetch_size

    autocomplete_field = config.search_methods.autocomplete.field
    autocomplete_operator = config.search_methods.autocomplete.operator

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

    if config.translation.enabled and config.translation.autocomplete_enabled:
        expansion = search_service.expand_multilingual_query(query_text)
        for phrase in expansion["phrase_translations"]:
            lexical_queries.append({
                "match": {
                    autocomplete_field: {
                        "query": phrase,
                        "operator": autocomplete_operator,
                        "boost": config.translation.phrase_boost,
                    }
                }
            })

    # Not: yalnızca manuel intent kuralları kullanılır — dinamik kategori
    # keşfi (Elasticsearch aggregation isteği) her tuş vuruşunda ekstra bir
    # istek anlamına geleceğinden BİLEREK autocomplete'e dahil edilmez.
    intent_boost_queries = []
    intent_exclusions = []
    if apply_intent_reranking:
        intent_boost_queries, intent_exclusions = search_service._build_intent_signals(query_text)

    return {
        "size": result_size,
        "track_total_hits": False,
        "timeout": f"{config.elasticsearch.autocomplete_timeout_seconds}s",
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


def fetch_suggestion_hits(query_text: str, result_size: int):
    """
    Canlı önerileri Edge NGram autocomplete indexinden (AUTOCOMPLETE_INDEX_NAME)
    getirir (ÖNBELLEKSİZ — Streamlit önbellekleme `app.py`'de yapılır, bkz.
    modül docstring'i). `get_suggestions`'ın varsayılan fetcher'ıdır.

    Cache güvenliği için yalnızca JSON-benzeri argümanlar/dönüşler kullanılır;
    API key ve bağlantı nesneleri parametre yapılmaz (ortam değişkeni globaldir).

    Dönüş: (hits_listesi, hata_mesaji)
    """
    payload = build_autocomplete_query(query_text, result_size=result_size)

    data, error = search_service._post_search(
        payload,
        timeout=search_service.CONFIG.elasticsearch.autocomplete_timeout_seconds,
        index=AUTOCOMPLETE_INDEX_NAME,
    )
    if error:
        return [], error
    return data.get("hits", {}).get("hits", []), None


def get_suggestions(
    query_text: str, max_items: int | None = None, *, fetch_hits=None
) -> tuple[list[SuggestionItem], str | None]:
    """
    Edge NGram autocomplete önerileri: title.autocomplete alanı üzerinden
    _score sıralı ürün önerileri (intent boost/dışlama ve çeviri alternatifleri
    korunur).

    Elasticsearch'ten config'teki autocomplete_fetch_size kadar hit istenir,
    Python'da benzersizleştirilir (parent_asin, yoksa title.casefold()) ve en
    fazla `max_items` (varsayılan: config'teki autocomplete_display_size)
    öneri döndürülür. Öneriler lexical arama switchlerinden bağımsızdır.

    `fetch_hits`: varsayılan `fetch_suggestion_hits` (önbelleksiz); `app.py`
    burada kendi `st.cache_data` sarmalayıcısını enjekte eder.

    Dönüş: (list[SuggestionItem], hata_mesaji|None)
    """
    fetch_hits = fetch_hits or fetch_suggestion_hits
    config = search_service.CONFIG
    max_items = max_items if max_items is not None else config.limits.autocomplete_display_size
    hits, error = fetch_hits(query_text, config.limits.autocomplete_fetch_size)
    if error:
        return [], error

    suggestions: list[SuggestionItem] = []
    seen = set()
    for hit in hits:
        src = hit.get("_source", {})
        asin = src.get("parent_asin")
        title = src.get("title")

        key = asin if asin else (title.casefold() if title else None)
        if key is None or key in seen:
            continue
        seen.add(key)

        suggestions.append(SuggestionItem(
            title=title or "Ürün adı bulunamadı",
            asin=asin or "",
            store=src.get("store"),
            price=src.get("price"),
            average_rating=src.get("average_rating"),
            image_url=src.get("image_url"),
            score=hit.get("_score", 0) or 0,
        ))
        if len(suggestions) >= max_items:
            break

    return suggestions, None
