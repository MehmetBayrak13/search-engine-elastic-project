"""
`components/search_input` custom component'i için testler:
  - `app._build_suggestion_payload`: html.escape kaçışlama, title_text
    kaçışsız kalması, image_url'in ESCAPE EDİLMEMESİ (innerHTML değil,
    <img>.src property'sine atanıyor)
  - frontend/index.html: tek panel container, öneri başına iframe/ayrı aksiyon
    butonu yok, overflow/ellipsis kuralları, JSON event sözleşmesi

Gerçek bir tarayıcı/DOM çalıştırılmaz — bu testler statik HTML/JS metnini
ve Python tarafındaki payload üretimini doğrular.
"""

from pathlib import Path

import app
from services.search_models import SuggestionItem

_FRONTEND_HTML = (
    Path(__file__).resolve().parent.parent
    / "components" / "search_input" / "frontend" / "index.html"
).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# app._build_suggestion_payload
# ---------------------------------------------------------------------------

def _item(**overrides):
    base = dict(
        title="Wireless <Headphones> & Mouse",
        asin="B0X",
        store="Acme & Co",
        price=29.99,
        average_rating=4.5,
        image_url="https://example.com/x.png?a=1&b=2",
        score=3.2,
    )
    base.update(overrides)
    return SuggestionItem(**base)


def test_title_html_is_escaped():
    payload = app._build_suggestion_payload([_item()])
    assert "&lt;Headphones&gt;" in payload[0]["title_html"]
    assert "&amp;" in payload[0]["title_html"]


def test_title_text_is_unescaped_raw_title():
    # title_text arama sorgusu olarak kullanılır — HTML entity İÇERMEMELİ.
    payload = app._build_suggestion_payload([_item()])
    assert payload[0]["title_text"] == "Wireless <Headphones> & Mouse"


def test_meta_html_escapes_store_name():
    payload = app._build_suggestion_payload([_item(store="A & B Mağazası")])
    assert "&amp;" in payload[0]["meta_html"]


def test_image_url_is_not_html_escaped():
    # <img>.src property'sine doğrudan atanır (innerHTML DEĞİL); html.escape
    # uygulanırsa URL'deki '&' -> '&amp;' olur ve query string bozulur.
    payload = app._build_suggestion_payload([_item(image_url="https://example.com/x.png?a=1&b=2")])
    assert payload[0]["image_url"] == "https://example.com/x.png?a=1&b=2"


def test_missing_image_url_falls_back_to_placeholder():
    payload = app._build_suggestion_payload([_item(image_url=None)])
    assert payload[0]["image_url"] == app.PLACEHOLDER_IMAGE


def test_asin_present_in_payload():
    payload = app._build_suggestion_payload([_item(asin="B0X")])
    assert payload[0]["asin"] == "B0X"


def test_zero_price_is_not_shown():
    payload = app._build_suggestion_payload([_item(price=0)])
    assert "💲" not in payload[0]["meta_html"]


# ---------------------------------------------------------------------------
# frontend/index.html — yapısal kontroller (statik metin analizi)
# ---------------------------------------------------------------------------

def test_single_panel_container_no_per_suggestion_iframe():
    assert _FRONTEND_HTML.count('id="si-panel"') == 1
    assert "<iframe" not in _FRONTEND_HTML.lower()


def test_no_separate_action_buttons_for_suggestions():
    assert "Bu ürünü ara" not in _FRONTEND_HTML
    assert "Amazon'da aç" not in _FRONTEND_HTML


def test_panel_scrolls_as_single_container():
    assert "overflow-y: auto" in _FRONTEND_HTML
    # satırların KENDİ scrollbar'ı olmamalı — panel dışında overflow taşması yok.
    assert ".si-row {" in _FRONTEND_HTML


def test_title_uses_line_clamp_ellipsis():
    assert "-webkit-line-clamp" in _FRONTEND_HTML
    assert "text-overflow: ellipsis" in _FRONTEND_HTML


def test_keyboard_navigation_handles_arrows_enter_escape():
    assert '"ArrowDown"' in _FRONTEND_HTML
    assert '"ArrowUp"' in _FRONTEND_HTML
    assert '"Enter"' in _FRONTEND_HTML
    assert '"Escape"' in _FRONTEND_HTML


def test_select_and_submit_close_panel_client_side():
    assert "closePanel()" in _FRONTEND_HTML


def test_event_contract_fields_present():
    assert '"typing"' in _FRONTEND_HTML
    assert '"submit"' in _FRONTEND_HTML
    assert '"select"' in _FRONTEND_HTML
    assert "event_id" in _FRONTEND_HTML


def test_no_parent_iframe_reaching_or_programmatic_button_click():
    # Enter güvenilirliği artık native component protokolüyle sağlanıyor —
    # eski bridge'in yaptığı gibi window.parent.document taraması veya
    # başka bir Streamlit widget'ını programatik tıklatma OLMAMALI.
    assert "window.parent.document" not in _FRONTEND_HTML
    assert ".click()" not in _FRONTEND_HTML


def test_panel_max_height_and_row_height_are_config_driven():
    assert "panelMaxHeightPx" in _FRONTEND_HTML
    assert "rowHeightPx" in _FRONTEND_HTML


def test_show_images_is_config_driven():
    assert "showImages" in _FRONTEND_HTML


# ---------------------------------------------------------------------------
# components/search_input/__init__.py — Python sarmalayıcı sözleşmesi
# ---------------------------------------------------------------------------

def test_search_input_wrapper_has_no_business_logic():
    from components import search_input as search_input_pkg
    import inspect as _inspect

    source = _inspect.getsource(search_input_pkg)
    # Component hiçbir ES sorgu/servis çağrısı içermemeli.
    assert "search_service" not in source
    assert "autocomplete_service" not in source
    assert "_post_search" not in source
