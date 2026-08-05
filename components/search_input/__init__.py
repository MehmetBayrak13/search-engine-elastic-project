"""
`search_input` Streamlit custom component'inin Python sarmalayıcısı.

Bu, GEÇİCİ ama TAŞINABİLİR bir component'tir: `frontend/` bugün build-step'siz
statik HTML/JS'dir (st_keyup'ın kullandığı buildless yaklaşımın aynısı), ama
Python <-> JS arasındaki sözleşme (bkz. CONTRACT.md) — JSON event protokolü
`{type, query, event_id, asin}` — React tabanlı bir component'e geçilirken
DEĞİŞMEDEN korunabilir: tek yapılması gereken `frontend/`i bir React build
çıktısıyla değiştirmek, bu dosyadaki `declare_component(..., path=...)`
çağrısı aynı kalır.

Bu component hiçbir arama/autocomplete İŞ MANTIĞI içermez (Elasticsearch
sorgusu yok, config okuma yok, Streamlit session_state yok); yalnızca
kullanıcı etkileşimini generic bir olaya çevirip Python'a bildirir.
Arama/autocomplete mantığı `services/` katmanındadır ve bu component'i
DEĞİL, çağıran `app.py`'yi kullanır — component servisleri doğrudan çağırmaz.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import streamlit.components.v1 as components

_COMPONENT_DIR = Path(__file__).resolve().parent
_FRONTEND_DIR = _COMPONENT_DIR / "frontend"

if not (_FRONTEND_DIR / "index.html").is_file():
    raise FileNotFoundError(
        f"Search input frontend missing: {_FRONTEND_DIR / 'index.html'}"
    )

_component_func = components.declare_component("search_input", path=str(_FRONTEND_DIR))


def search_input(
    *,
    value: str = "",
    suggestions: list[dict[str, Any]] | None = None,
    placeholder: str = "",
    debounce_ms: int = 300,
    panel_max_height_px: int = 360,
    row_height_px: int = 56,
    show_images: bool = True,
    key: str | None = None,
) -> dict[str, Any] | None:
    """Tek arama kutusu + Chrome-tarzı öneri dropdown'unu render eder.

    `value`: yalnızca component'in İLK mount'unda (yeni `key`) input'un
    başlangıç metni olarak kullanılır; aynı `key` ile sonraki render'larda
    kullanıcının o an yazmakta olduğu metnin üzerine YAZILMAZ (bkz.
    frontend/index.html `hasMounted` mantığı) — canlı yazım kesintiye uğramaz.

    `suggestions`: [{"title_html": str, "title_text": str, "meta_html": str,
                      "image_url": str, "asin": str}, ...]. `title_html`/
    `meta_html` ÇAĞIRAN TARAFTA (app.py) `html.escape` ile kaçışlanmış
    olmalı — bu fonksiyon kendisi kaçışlama yapmaz. `title_text` kaçışsız
    ham başlıktır; bir öneri seçildiğinde arama sorgusu olarak kullanılır.

    Dönüş: en son client-side olayı temsil eden
    `{"type": "typing"|"submit"|"select", "query": str, "event_id": str,
      "asin": str|None}` sözlüğü, ya da henüz hiç olay yoksa `None`.
    `event_id` her olayda benzersizdir (Streamlit'in aynı değer için rerun
    atlamasını önler — ör. aynı sorgu art arda iki kez explicit aranırsa).
    """
    return _component_func(
        value=value,
        suggestions=suggestions or [],
        placeholder=placeholder,
        debounceMs=debounce_ms,
        panelMaxHeightPx=panel_max_height_px,
        rowHeightPx=row_height_px,
        showImages=show_images,
        key=key,
        default=None,
    )
