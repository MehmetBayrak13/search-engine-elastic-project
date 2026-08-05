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

import inspect
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



# ---------------------------------------------------------------------------
# Event/rerun akışı — component <-> app.py döngüsünün gerçek titreme/loop
# kaynağını kapatan davranışlar. Bkz. `_handle_search_input_event` ve
# `_suggestions_feedback_changed` docstring'leri.
# ---------------------------------------------------------------------------

def test_initial_mount_never_emits_from_onrender():
    # `onRender` yalnızca `hasMounted` false iken input.value'yu set eder;
    # bunun DIŞINDA (mount dahil) hiçbir yerde `emit(` çağırmaz — typing/submit/
    # select event'leri yalnızca gerçek kullanıcı etkileşimi (input/keydown/
    # click) listener'larından tetiklenir.
    render_start = _FRONTEND_HTML.index("function onRender(")
    render_end = _FRONTEND_HTML.index("function applyTheme(")
    on_render_body = _FRONTEND_HTML[render_start:render_end]
    assert "emit(" not in on_render_body


def test_suggestions_prop_update_alone_does_not_emit():
    # `suggestions` prop'u yeniden geldiğinde (`onRender` içindeki
    # `suggestions = Array.isArray(...)` ataması) yalnızca panel render
    # edilir (`openPanelIfAny` / `renderRows`) — component değeri set edilmez.
    render_start = _FRONTEND_HTML.index("function onRender(")
    render_end = _FRONTEND_HTML.index("function applyTheme(")
    on_render_body = _FRONTEND_HTML[render_start:render_end]
    assert "setComponentValue" not in on_render_body
    assert "openPanelIfAny" in on_render_body


def test_repeated_typing_event_id_is_ignored_and_state_untouched(monkeypatch):
    session_state = {
        "_search_input_last_event_id": "t1",
        "current_page": 6,
        "run_search": False,
    }
    monkeypatch.setattr(app.st, "session_state", session_state)
    event = {"type": "typing", "query": "gaming mo", "event_id": "t1", "asin": None}

    query, is_new_event = app._handle_search_input_event(event)

    assert query == "gaming mo"
    assert is_new_event is False
    # Aynı event_id ikinci kez görülünce hiçbir state değişmemeli.
    assert session_state["current_page"] == 6
    assert session_state["run_search"] is False


def test_typing_event_processed_exactly_once_across_reruns(monkeypatch):
    # Bir "typing" olayı, aynı component değeri (aynı event_id) Streamlit
    # tarafından sonraki reruns'larda tekrar döndürüldüğünde yalnızca İLK
    # seferinde "yeni" sayılmalı.
    session_state = {}
    monkeypatch.setattr(app.st, "session_state", session_state)
    event = {"type": "typing", "query": "kamera", "event_id": "t2", "asin": None}

    _, first_is_new = app._handle_search_input_event(event)
    _, second_is_new = app._handle_search_input_event(event)
    _, third_is_new = app._handle_search_input_event(event)

    assert first_is_new is True
    assert second_is_new is False
    assert third_is_new is False


def test_unchanged_suggestions_do_not_require_rerun():
    payload = [{"asin": "B0X", "title_text": "Wireless Mouse"}]
    assert app._suggestions_feedback_changed(payload, payload, False, False) is False
    assert app._suggestions_feedback_changed(list(payload), list(payload), False, False) is False


def test_changed_suggestions_require_rerun():
    old_payload = [{"asin": "B0X", "title_text": "Wireless Mouse"}]
    new_payload = [{"asin": "B0Y", "title_text": "Gaming Mouse"}]
    assert app._suggestions_feedback_changed(old_payload, new_payload, False, False) is True


def test_error_state_transition_requires_rerun_even_if_payload_matches():
    payload: list = []
    assert app._suggestions_feedback_changed(payload, payload, False, True) is True
    assert app._suggestions_feedback_changed(payload, payload, True, False) is True


def test_typing_rerun_is_conditional_on_suggestions_change_in_main_source():
    # Kök neden: her "typing" olayı için KOŞULSUZ st.rerun() çağrısı, zaten
    # component'in kendi benzersiz event_id'sinin tetiklediği otomatik
    # rerun'un ÜSTÜNE binip her tuş vuruşunda sayfayı iki kez çiziyordu. Bu
    # artık `_suggestions_feedback_changed` ile koşullu olmalı.
    source = inspect.getsource(app.main)
    typing_block_start = source.index('event_type == "typing" and show_suggestions')
    typing_block_end = source.index("elif event_type in", typing_block_start)
    typing_block = source[typing_block_start:typing_block_end]
    assert "_suggestions_feedback_changed(" in typing_block
    assert "if _suggestions_feedback_changed(" in typing_block


def test_search_input_frontend_dir_resolves_regardless_of_cwd():
    # `declare_component`'e verilen path, çalışma dizininden (os.getcwd())
    # değil, __init__.py'nin kendi konumundan türetilmeli — aksi halde
    # Streamlit Cloud'da farklı bir cwd'den başlatıldığında component
    # frontend'i bulamaz ("having trouble loading" hatası).
    import os

    from components import search_input as search_input_pkg

    frontend_dir = search_input_pkg._FRONTEND_DIR
    assert frontend_dir.is_absolute()
    assert (frontend_dir / "index.html").is_file()

    cwd = os.getcwd()
    try:
        os.chdir(str(Path(__file__).resolve().parent))
        assert (search_input_pkg._FRONTEND_DIR / "index.html").is_file()
    finally:
        os.chdir(cwd)
