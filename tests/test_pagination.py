"""
Sayfalama (`from + size`) testleri. Gerçek Elastic Cloud'a HİÇBİR istek
atılmaz — `app._post_search` mock'lanır (bkz. `_mock_post_search`).
"""

import dataclasses
import inspect
import re

import pytest

import app


def _with_pagination(**overrides):
    """`app.CONFIG.pagination`ı geçici olarak override eden bir AppConfig döner."""
    pagination = dataclasses.replace(app.CONFIG.pagination, **overrides)
    return dataclasses.replace(app.CONFIG, pagination=pagination)


@pytest.fixture(autouse=True)
def _restore_config():
    original = app.CONFIG
    yield
    app.CONFIG = original


def _mock_post_search(monkeypatch, response=({"hits": {"hits": [], "total": {"value": 0}}}, None)):
    """`app._post_search`ü mock'lar ve yapılan çağrıları kaydeder."""
    calls = []

    def fake_post_search(payload, timeout=20, index=None):
        calls.append({"payload": payload, "timeout": timeout, "index": index})
        return response

    monkeypatch.setattr(app, "_post_search", fake_post_search)
    return calls


def _hits(n, start=0):
    return [{"_id": f"id{i}", "_score": 1.0, "_source": {"parent_asin": f"A{i}"}} for i in range(start, start + n)]


# ---------------------------------------------------------------------------
# Query: from/size hesaplama
# ---------------------------------------------------------------------------

def test_page_1_produces_from_0():
    payload = app.build_search_query("kamera", page=1, apply_intent_reranking=False)
    assert payload["from"] == 0


def test_page_2_produces_from_page_size():
    payload = app.build_search_query("kamera", page=2, apply_intent_reranking=False)
    assert payload["from"] == app.CONFIG.pagination.page_size


def test_page_3_produces_from_2x_page_size():
    payload = app.build_search_query("kamera", page=3, apply_intent_reranking=False)
    assert payload["from"] == 2 * app.CONFIG.pagination.page_size


def test_size_comes_from_pagination_page_size():
    payload = app.build_search_query("kamera", page=1, apply_intent_reranking=False)
    assert payload["size"] == app.CONFIG.pagination.page_size


def test_size_overrides_result_size_when_pagination_enabled():
    # pagination.page_size, limits.result_size'dan farklı bile olsa kazanır —
    # bu yüzden iki alan asla "çelişmez" (bkz. config.PaginationConfig).
    payload = app.build_search_query(
        "kamera", page=1, result_size=999, apply_intent_reranking=False
    )
    assert payload["size"] == app.CONFIG.pagination.page_size
    assert payload["size"] != 999


def test_page_below_one_normalizes_to_page_1():
    payload_zero = app.build_search_query("kamera", page=0, apply_intent_reranking=False)
    payload_negative = app.build_search_query("kamera", page=-5, apply_intent_reranking=False)
    assert payload_zero["from"] == 0
    assert payload_negative["from"] == 0


def test_pagination_disabled_omits_from_and_uses_result_size():
    app.CONFIG = _with_pagination(enabled=False)
    payload = app.build_search_query(
        "kamera", page=3, result_size=42, apply_intent_reranking=False
    )
    assert "from" not in payload
    assert payload["size"] == 42


def test_max_result_window_exceeded_raises_pagination_limit_error():
    app.CONFIG = _with_pagination(page_size=20, max_result_window=100)
    # max_allowed_page = 100 // 20 = 5; sayfa 6 -> from=100, from+size=120 > 100
    with pytest.raises(app.PaginationLimitError):
        app.build_search_query("kamera", page=6, apply_intent_reranking=False)


def test_max_allowed_last_page_does_not_raise():
    app.CONFIG = _with_pagination(page_size=20, max_result_window=100)
    # max_allowed_page = 5; from=(5-1)*20=80, from+size=100 == max_result_window (izinli)
    payload = app.build_search_query("kamera", page=5, apply_intent_reranking=False)
    assert payload["from"] == 80
    assert payload["size"] == 20


def test_pagination_limit_error_reports_requested_and_max_page():
    app.CONFIG = _with_pagination(page_size=20, max_result_window=100)
    with pytest.raises(app.PaginationLimitError) as excinfo:
        app.build_search_query("kamera", page=99, apply_intent_reranking=False)
    assert excinfo.value.requested_page == 99
    assert excinfo.value.max_allowed_page == 5


def test_autocomplete_query_never_includes_from():
    payload = app.build_autocomplete_query("kam", apply_intent_reranking=False)
    assert "from" not in payload


def test_autocomplete_size_unaffected_by_pagination():
    payload = app.build_autocomplete_query("kam")
    assert payload["size"] == app.CONFIG.limits.autocomplete_fetch_size


# ---------------------------------------------------------------------------
# search_products metadata
# ---------------------------------------------------------------------------

def test_total_pages_calculated_correctly(monkeypatch):
    _mock_post_search(
        monkeypatch,
        ({"hits": {"hits": _hits(20), "total": {"value": 45}}}, None),
    )
    result = app.search_products("kamera", page=1)
    assert result.total_pages == 3  # ceil(45/20)


def test_start_end_item_correct_for_middle_page(monkeypatch):
    _mock_post_search(
        monkeypatch,
        ({"hits": {"hits": _hits(20), "total": {"value": 45}}}, None),
    )
    result = app.search_products("kamera", page=2)
    assert result.start_item == 21
    assert result.end_item == 40


def test_last_page_with_partial_results_has_correct_end_item(monkeypatch):
    # total=45, page_size=20 -> sayfa 3'te yalnızca 5 sonuç var.
    _mock_post_search(
        monkeypatch,
        ({"hits": {"hits": _hits(5), "total": {"value": 45}}}, None),
    )
    result = app.search_products("kamera", page=3)
    assert result.start_item == 41
    assert result.end_item == 45
    assert len(result.hits) == 5


def test_has_previous_and_has_next_first_page(monkeypatch):
    _mock_post_search(
        monkeypatch,
        ({"hits": {"hits": _hits(20), "total": {"value": 45}}}, None),
    )
    result = app.search_products("kamera", page=1)
    assert result.has_previous is False
    assert result.has_next is True


def test_has_previous_and_has_next_last_page(monkeypatch):
    _mock_post_search(
        monkeypatch,
        ({"hits": {"hits": _hits(5), "total": {"value": 45}}}, None),
    )
    result = app.search_products("kamera", page=3)
    assert result.has_previous is True
    assert result.has_next is False


def test_total_zero_produces_zero_pages_and_no_navigation(monkeypatch):
    _mock_post_search(
        monkeypatch,
        ({"hits": {"hits": [], "total": {"value": 0}}}, None),
    )
    result = app.search_products("kamera", page=1)
    assert result.total == 0
    assert result.total_pages == 0
    assert result.has_previous is False
    assert result.has_next is False
    assert result.start_item == 0
    assert result.end_item == 0


def test_current_page_and_page_size_reflected_in_result(monkeypatch):
    _mock_post_search(
        monkeypatch,
        ({"hits": {"hits": _hits(20), "total": {"value": 45}}}, None),
    )
    result = app.search_products("kamera", page=2)
    assert result.current_page == 2
    assert result.page_size == app.CONFIG.pagination.page_size


def test_total_pages_capped_at_max_allowed_page(monkeypatch):
    app.CONFIG = _with_pagination(page_size=20, max_result_window=100)
    # total çok büyük (1_000_000) olsa da max_allowed_page = 5'i aşmamalı.
    _mock_post_search(
        monkeypatch,
        ({"hits": {"hits": _hits(20), "total": {"value": 1_000_000}}}, None),
    )
    result = app.search_products("kamera", page=1)
    assert result.total_pages == 5


def test_deep_pagination_beyond_window_returns_friendly_error_not_crash(monkeypatch):
    app.CONFIG = _with_pagination(page_size=20, max_result_window=100)
    calls = _mock_post_search(monkeypatch)
    result = app.search_products("kamera", page=999)
    assert result.hits is None
    assert result.error  # anlaşılır bir mesaj döner
    assert calls == []  # Elasticsearch'e hiç istek atılmadı


def test_search_error_from_elasticsearch_preserves_pagination_metadata(monkeypatch):
    _mock_post_search(monkeypatch, (None, "bağlantı hatası"))
    result = app.search_products("kamera", page=2)
    assert result.hits is None
    assert result.error == "bağlantı hatası"
    assert result.current_page == 2


# ---------------------------------------------------------------------------
# State/UI mantığı (saf yardımcı fonksiyonlar)
# ---------------------------------------------------------------------------

def test_new_query_resets_page_to_one():
    assert app._resolve_page_for_new_search("eski sorgu", "yeni sorgu", 5) == 1


def test_same_query_preserves_requested_page():
    assert app._resolve_page_for_new_search("aynı sorgu", "aynı sorgu", 5) == 5


def test_same_query_with_invalid_requested_page_normalizes_to_one():
    assert app._resolve_page_for_new_search("aynı sorgu", "aynı sorgu", 0) == 1


def test_first_ever_search_with_no_previous_query_starts_at_page_one():
    assert app._resolve_page_for_new_search(None, "ilk sorgu", 5) == 1


def test_next_page_increments():
    assert app._next_page(3) == 4


def test_previous_page_decrements():
    assert app._previous_page(3) == 2


def test_previous_page_never_goes_below_one():
    assert app._previous_page(1) == 1


def test_settings_changed_detects_signature_mismatch():
    assert app._settings_changed(("a", True), ("a", False)) is True


def test_settings_unchanged_when_signature_matches():
    assert app._settings_changed(("a", True), ("a", True)) is False


# ---------------------------------------------------------------------------
# Regresyon: mevcut arama davranışı pagination ile bozulmuyor
# ---------------------------------------------------------------------------

def test_lexical_bool_must_preserved_with_page_param():
    payload = app.build_search_query("kamera", page=2, apply_intent_reranking=False)
    must = payload["query"]["bool"]["must"]
    assert len(must) == 1
    assert "should" in must[0]["bool"]
    assert must[0]["bool"]["minimum_should_match"] == 1


def test_exact_asin_preserved_with_page_param():
    payload = app.build_search_query("B000123456", page=2, apply_intent_reranking=False)
    lexical = payload["query"]["bool"]["must"][0]["bool"]["should"]
    asin_clauses = [c for c in lexical if "term" in c]
    assert len(asin_clauses) == 1
    field = app.CONFIG.search_methods.exact_asin.field
    assert asin_clauses[0]["term"][field]["value"] == "B000123456"


def test_turkish_expansion_preserved_with_page_param():
    payload = app.build_search_query("kablosuz kulaklık", page=2, apply_intent_reranking=False)
    lexical = payload["query"]["bool"]["must"][0]["bool"]["should"]
    assert len(lexical) >= 5  # 4 standart lexical + en az 1 çeviri alternatifi


def test_dynamic_category_discovery_preserved_with_pagination(monkeypatch):
    calls = _mock_post_search(
        monkeypatch,
        ({"hits": {"hits": [], "total": {"value": 0}}}, None),
    )
    app.search_products("toilet paper", page=2)
    # search_products, resolve_intent_signals üzerinden discovery aggregation
    # isteğini de atar; en az bir çağrı autocomplete index'i DEĞİL, ana
    # index'e (discovery) veya arama index'ine gitmiş olmalı.
    assert calls, "search_products hiç Elasticsearch çağrısı yapmadı"


def test_quality_ranking_stays_disabled_by_default_with_pagination():
    assert app.CONFIG.quality_ranking.enabled is False
    payload = app.build_search_query("kamera", page=2, apply_intent_reranking=False)
    assert "function_score" not in payload["query"]


def test_discovery_filter_stays_disabled_by_default_with_pagination():
    assert app.CONFIG.quality_ranking.discovery_filter_enabled is False


def test_exact_asin_quality_bypass_preserved_with_pagination():
    quality = dataclasses.replace(app.CONFIG.quality_ranking, enabled=True, bypass_for_exact_asin=True)
    app.CONFIG = dataclasses.replace(app.CONFIG, quality_ranking=quality)
    payload = app.build_search_query(
        "B000123456", page=1, enable_exact_asin=True, apply_intent_reranking=False
    )
    functions = payload["query"]["function_score"]["functions"]
    for function in functions:
        filt = function["filter"]
        must_not = filt.get("bool", filt).get("must_not") if isinstance(filt, dict) else None
        assert must_not, f"beklenen bypass must_not bulunamadı: {function}"


# ---------------------------------------------------------------------------
# Regresyon: _scroll_results_to_top() StreamlitInvalidHeightError üretmiyor
# (bkz. production hatası: st.iframe(height=0), Streamlit 1.60'ta
# StreamlitInvalidHeightError fırlatıyor)
# ---------------------------------------------------------------------------

def test_scroll_results_to_top_does_not_raise():
    app._scroll_results_to_top()


def test_scroll_results_to_top_does_not_use_invalid_height(monkeypatch):
    # Önceki implementasyon st.iframe(..., height=0) kullanıyordu ve
    # Streamlit 1.60'ta StreamlitInvalidHeightError'a yol açıyordu. Yeni
    # implementasyon çağrı yapmalı ama height literal 0 ya da (tercih
    # edilmeyen) 1 "sihirli sayısı" olmamalı.
    calls = []
    monkeypatch.setattr(app.st, "iframe", lambda *a, **k: calls.append((a, k)))
    app._scroll_results_to_top()
    assert len(calls) == 1
    _, kwargs = calls[0]
    height = kwargs.get("height")
    assert height not in (0, 1)
    assert height in ("content", "stretch") or (isinstance(height, int) and height > 0)


def test_scroll_results_to_top_targets_result_header_anchor(monkeypatch):
    calls = []
    monkeypatch.setattr(app.st, "iframe", lambda *a, **k: calls.append((a, k)))
    app._scroll_results_to_top()
    (html_arg,), _ = calls[0]
    assert app._RESULTS_TOP_ANCHOR_ID in html_arg


def test_scroll_results_to_top_swallows_iframe_exception(monkeypatch):
    # Component render'ı (örn. tarayıcı/ortam kaynaklı) başarısız olsa bile
    # pagination akışı kesilmemeli.
    def boom(*args, **kwargs):
        raise RuntimeError("component render failed")

    monkeypatch.setattr(app.st, "iframe", boom)
    app._scroll_results_to_top()  # exception fırlatmamalı


def test_page_2_transition_still_flags_scroll_and_does_not_raise(monkeypatch):
    # render_pagination_bar'daki "Sonraki" butonunun tetiklediği state
    # değişikliğini simüle eder: sayfa artar, scroll_to_top_once set edilir,
    # ardından tüketilen _scroll_results_to_top() hata üretmemeli.
    monkeypatch.setattr(app.st, "session_state", {})
    app.st.session_state["current_page"] = app._next_page(1)
    app.st.session_state["scroll_to_top_once"] = True
    assert app.st.session_state["current_page"] == 2
    if app.st.session_state.pop("scroll_to_top_once", False):
        app._scroll_results_to_top()


def test_render_pagination_bar_next_button_flags_scroll_to_top(monkeypatch):
    # "Sonraki" butonuna basılınca sayfa ilerlemeli ve bir sonraki render'ın
    # scroll tetiklemesi için scroll_to_top_once bayrağı set edilmeli.
    monkeypatch.setattr(app.st, "session_state", {
        "search_total_pages": 3,
        "current_page": 1,
        "search_start_item": 1,
        "search_end_item": 20,
        "search_has_previous": False,
        "search_has_next": True,
    })
    monkeypatch.setattr(app.st, "button", lambda label, **k: "pg_next_" in k.get("key", ""))
    monkeypatch.setattr(app.st, "rerun", lambda: None)
    app.render_pagination_bar("bottom")
    assert app.st.session_state.get("current_page") == 2
    assert app.st.session_state.get("scroll_to_top_once") is True


def test_render_pagination_bar_prev_button_flags_scroll_to_top(monkeypatch):
    monkeypatch.setattr(app.st, "session_state", {
        "search_total_pages": 3,
        "current_page": 2,
        "search_start_item": 21,
        "search_end_item": 40,
        "search_has_previous": True,
        "search_has_next": True,
    })
    monkeypatch.setattr(app.st, "button", lambda label, **k: "pg_prev_" in k.get("key", ""))
    monkeypatch.setattr(app.st, "rerun", lambda: None)
    app.render_pagination_bar("bottom")
    assert app.st.session_state.get("current_page") == 1
    assert app.st.session_state.get("scroll_to_top_once") is True


def test_render_pagination_bar_renders_without_raising(monkeypatch):
    monkeypatch.setattr(app.st, "session_state", {
        "search_total_pages": 3,
        "current_page": 2,
        "search_start_item": 21,
        "search_end_item": 40,
        "search_has_previous": True,
        "search_has_next": True,
    })
    app.render_pagination_bar("top")


def test_render_result_header_emits_scroll_anchor(monkeypatch):
    calls = []
    monkeypatch.setattr(app.st, "markdown", lambda content, **k: calls.append(content))
    app.render_result_header("kamera", 10, 10, {"enable_phrase": True}, None)
    assert calls, "render_result_header hiç st.markdown çağırmadı"
    assert f'id="{app._RESULTS_TOP_ANCHOR_ID}"' in calls[0]


# ---------------------------------------------------------------------------
# Regresyon: pagination kontrolü yalnızca sonuçların ALTINDA render ediliyor
# (önceden main() aynı bar'ı hem üstte hem altta çiziyordu)
# ---------------------------------------------------------------------------

def test_main_renders_pagination_bar_exactly_once_at_bottom():
    source = inspect.getsource(app.main)
    positions = re.findall(r'render_pagination_bar\(\s*"([^"]+)"\s*\)', source)
    assert positions == ["bottom"]


def test_main_triggers_scroll_after_result_header_not_before():
    # Anchor, render_result_header tarafından çizilir; scroll tetikleyicisi
    # anchor DOM'a eklenmeden önce çalışmamalı.
    source = inspect.getsource(app.main)
    header_idx = source.index("render_result_header(")
    scroll_idx = source.index("_scroll_results_to_top()")
    assert header_idx < scroll_idx


def test_exact_asin_page_2_returns_empty_hits_without_crash(monkeypatch):
    # Tek kayıt döndüren bir exact ASIN sorgusunda page=2 istemek (from=20)
    # Elasticsearch'ten boş hits ile total=1 döner; bu çökmemeli, tutarlı
    # şekilde boş sonuç olarak ele alınmalı.
    _mock_post_search(
        monkeypatch,
        ({"hits": {"hits": [], "total": {"value": 1}}}, None),
    )
    result = app.search_products("B000123456", page=2)
    assert result.error is None
    assert result.hits == []
    assert result.total == 1
