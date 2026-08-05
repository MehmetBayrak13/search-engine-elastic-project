"""
JSON-safe request/response tipleri — `search_service` ve `autocomplete_service`
arasında paylaşılan veri sözleşmeleri.

Bu modül Streamlit'e bağımlı DEĞİLDİR (import streamlit yok, session_state
yok); yalnızca `dataclasses`/stdlib tipleri kullanır. İleride bir FastAPI
endpoint'i de aynı tipleri response modeli olarak kullanabilir.
"""

from __future__ import annotations

from dataclasses import dataclass


class PaginationLimitError(Exception):
    """`from + size`, `pagination.max_result_window`'ı aştığında
    `search_service.build_search_query` tarafından fırlatılır — Elasticsearch'in
    varsayılan `index.max_result_window` sınırı nedeniyle bu istek hiç
    gönderilmez. `search_service.search_products` bunu yakalayıp anlaşılır bir
    `SearchResult(error=...)` döner (bkz. CONFIG.ui.messages.pagination_limit_error)."""

    def __init__(self, requested_page: int, max_allowed_page: int):
        self.requested_page = requested_page
        self.max_allowed_page = max_allowed_page
        super().__init__(
            f"page {requested_page} sınırı aşıyor (maksimum sayfa: {max_allowed_page})"
        )


@dataclass
class SearchResult:
    """`search_service.search_products`ın dönüşü. `hits`/`total`/`error` bir
    hata durumunda `hits=None` anlamına gelir; geri kalan alanlar sayfalama
    metadata'sıdır. Tamamı JSON-safe (yalnızca primitive/list/dict alanlar)."""

    hits: list[dict] | None
    total: int
    error: str | None
    current_page: int
    page_size: int
    total_pages: int
    start_item: int
    end_item: int
    has_previous: bool
    has_next: bool


@dataclass(frozen=True)
class SuggestionItem:
    """`autocomplete_service.get_suggestions`ın döndürdüğü tek bir öneri.
    JSON-safe; UI katmanı (app.py) bunu component'e geçmeden önce
    html.escape ile kaçışlar."""

    title: str
    asin: str
    store: str | None
    price: float | None
    average_rating: float | None
    image_url: str | None
    score: float
