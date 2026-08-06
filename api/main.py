"""
Ürün Arama — React frontend'i besleyen FastAPI backend'i.

Çalıştırma:
    pip install -r requirements-api.txt

    # Linux / macOS:
    #   export ELASTICSEARCH_URL="https://<deployment>.es.<region>.cloud.es.io"
    #   export ELASTICSEARCH_API_KEY="<api_key>"
    #   export ALLOWED_ORIGINS="http://localhost:5173"
    # Windows (PowerShell):
    #   $env:ELASTICSEARCH_URL="https://<deployment>.es.<region>.cloud.es.io"
    #   $env:ELASTICSEARCH_API_KEY="<api_key>"
    #   $env:ALLOWED_ORIGINS="http://localhost:5173"

    uvicorn api.main:app --reload --port 8000

Bu dosya yalnızca HTTP sözleşmesi + CORS + önbellekleme yapar. Sorgu
oluşturma/intent/çeviri/kategori-keşif mantığının tamamı
`services/search_service.py` ve `services/autocomplete_service.py`da
yaşar (Streamlit UI'ının kullandığıyla AYNI kod yolu) — bkz. CLAUDE.md
"Search app" mimari bölümü.
"""

from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from api.cache import ttl_cache
from services import autocomplete_service, search_service

CONFIG = search_service.CONFIG
CONFIG_ERROR = search_service.CONFIG_ERROR
INTENT_RULES = search_service.INTENT_RULES

app = FastAPI(title="Ürün Arama API")

_allowed_origins = [
    origin.strip()
    for origin in os.getenv("ALLOWED_ORIGINS", "http://localhost:5173").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_methods=["GET"],
    allow_headers=["*"],
)

# `app.py`deki `_fetch_category_aggregations` / `_fetch_suggestion_hits`
# st.cache_data sarmalayıcılarının karşılığı — aynı TTL'ler config'ten.
_DISCOVERY_CACHE_TTL = CONFIG.dynamic_intent.cache_ttl_seconds if CONFIG else 300
_AUTOCOMPLETE_CACHE_TTL = CONFIG.limits.autocomplete_cache_ttl_seconds if CONFIG else 30


@ttl_cache(_DISCOVERY_CACHE_TTL)
def _fetch_category_aggregations(query_text: str, extra_query_texts: tuple[str, ...]):
    return search_service.fetch_category_aggregations(query_text, extra_query_texts)


@ttl_cache(_AUTOCOMPLETE_CACHE_TTL)
def _fetch_suggestion_hits(query_text: str, result_size: int):
    return autocomplete_service.fetch_suggestion_hits(query_text, result_size)


def _check_configuration() -> str | None:
    if CONFIG_ERROR:
        if CONFIG is not None:
            return CONFIG.ui.message("config_load_error", error=CONFIG_ERROR)
        return f"Yapılandırma yüklenemedi: {CONFIG_ERROR}"
    missing = []
    if not search_service.ES_URL:
        missing.append("ELASTICSEARCH_URL")
    if not search_service.ES_API_KEY:
        missing.append("ELASTICSEARCH_API_KEY")
    if missing:
        return CONFIG.ui.message("config_missing_env", missing=", ".join(missing))
    return None


def _require_config():
    error = _check_configuration()
    if error:
        raise HTTPException(status_code=503, detail=error)
    return CONFIG


def _hit_to_product(hit: dict) -> dict:
    source = hit.get("_source", {})
    asin = source.get("parent_asin") or hit.get("_id") or ""
    return {
        "asin": asin,
        "title": source.get("title") or "Ürün adı bulunamadı",
        "store": source.get("store"),
        "main_category": source.get("main_category"),
        "average_rating": source.get("average_rating"),
        "rating_number": source.get("rating_number"),
        "price": source.get("price"),
        "image_url": source.get("image_url") or CONFIG.ui.placeholder_image,
        "score": hit.get("_score", 0) or 0,
        "product_url": CONFIG.product_url_template.format(asin=asin) if asin else "",
    }


@app.get("/api/health")
def health():
    error = _check_configuration()
    return {"ok": error is None, "error": error}


@app.get("/api/config")
def get_config():
    """Frontend'in ihtiyaç duyduğu, sır İÇERMEYEN tüm görüntüleme/limit
    yapılandırması. `config/search_config.json`daki `ui`/`limits`/
    `pagination`/`autocomplete_ui` bölümlerinin bir yansımasıdır."""
    config = _require_config()
    ui = config.ui
    return {
        "hero": {
            "logo": ui.hero_logo,
            "title": ui.hero_title,
            "subtitle": ui.hero_subtitle,
        },
        "search_placeholder": ui.search_placeholder,
        "example_queries": list(ui.example_queries),
        "debounce_ms": ui.debounce_ms,
        "intent_fallback_icon": ui.intent_fallback_icon,
        "labels": ui.labels,
        "help_text": ui.help_text,
        "messages": ui.messages,
        "autocomplete_ui": {
            "panel_max_height_px": config.autocomplete_ui.panel_max_height_px,
            "row_height_px": config.autocomplete_ui.row_height_px,
            "show_images": config.autocomplete_ui.show_images,
        },
        "limits": {
            "result_size": config.limits.result_size,
            "autocomplete_display_size": config.limits.autocomplete_display_size,
            "autocomplete_min_chars": config.limits.autocomplete_min_chars,
        },
        "pagination": {
            "enabled": config.pagination.enabled,
            "max_result_window": config.pagination.max_result_window,
        },
        "elasticsearch": {"search_index_count": len(config.elasticsearch.search_indices)},
    }


@app.get("/api/search")
def search(
    q: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    enable_phrase: bool = Query(default=True),
    enable_multi_match: bool = Query(default=True),
    enable_fuzzy: bool = Query(default=True),
    enable_exact_asin: bool = Query(default=True),
):
    config = _require_config()
    query_text = (q or "").strip()

    if not query_text:
        raise HTTPException(status_code=400, detail=config.ui.message("empty_query_warning"))
    if not any([enable_phrase, enable_multi_match, enable_fuzzy, enable_exact_asin]):
        raise HTTPException(status_code=400, detail=config.ui.message("no_method_warning"))

    result = search_service.search_products(
        query_text,
        enable_phrase=enable_phrase,
        enable_multi_match=enable_multi_match,
        enable_fuzzy=enable_fuzzy,
        enable_exact_asin=enable_exact_asin,
        page=page,
        fetch_aggregations=_fetch_category_aggregations,
    )

    if result.error:
        raise HTTPException(status_code=502, detail=result.error)

    intent_info = search_service.detect_search_intent(query_text)
    intent_name = intent_info.get("intent")
    intent_payload = None
    if intent_name and intent_name in INTENT_RULES:
        rule = INTENT_RULES[intent_name]
        intent_payload = {"name": intent_name, "label": rule.label, "icon": rule.icon or config.ui.intent_fallback_icon}

    return {
        "query": query_text,
        "total": result.total,
        "hits": [_hit_to_product(hit) for hit in (result.hits or [])],
        "current_page": result.current_page,
        "page_size": result.page_size,
        "total_pages": result.total_pages,
        "start_item": result.start_item,
        "end_item": result.end_item,
        "has_previous": result.has_previous,
        "has_next": result.has_next,
        "intent": intent_payload,
    }


@app.get("/api/autocomplete")
def autocomplete(q: str = Query(default="")):
    config = _require_config()
    query_text = (q or "").strip()
    min_chars = config.limits.autocomplete_min_chars
    if len(query_text) < min_chars:
        return {"suggestions": [], "min_chars": min_chars}

    suggestions, error = autocomplete_service.get_suggestions(
        query_text, fetch_hits=_fetch_suggestion_hits
    )
    if error:
        raise HTTPException(status_code=502, detail=config.ui.message("suggestions_unavailable_warning"))

    return {
        "suggestions": [
            {
                "asin": item.asin,
                "title": item.title,
                "store": item.store,
                "price": item.price,
                "average_rating": item.average_rating,
                "image_url": item.image_url or config.ui.placeholder_image,
            }
            for item in suggestions
        ],
        "min_chars": min_chars,
    }
