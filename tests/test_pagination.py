"""
Sayfalama (`from + size`) testleri. Gerçek Elastic Cloud'a HİÇBİR istek
atılmaz — `search_service._post_search` mock'lanır (bkz. `_mock_post_search`).

Sorgu oluşturma/çalıştırma mantığı `services/search_service.py` ve
`services/autocomplete_service.py`de yaşıyor (bkz. proje mimarisi); bu
dosya yalnızca o katmanı test eder. Streamlit'e özgü UI/session_state
orkestrasyonu (eski `app.py`) ve onun testleri, Streamlit deployment'ı
kullanımdan kalktığı için tamamen kaldırıldı — o davranışın React
tarafında birebir Python karşılığı yok (React kendi state'ini
`frontend/src/App.jsx` içinde JavaScript ile yönetiyor).
"""

import dataclasses

import pytest

from services import autocomplete_service, search_service


def _with_pagination(**overrides):
    """`search_service.CONFIG.pagination`ı geçici olarak override eden bir AppConfig döner."""
    pagination = dataclasses.replace(search_service.CONFIG.pagination, **overrides)
    return dataclasses.replace(search_service.CONFIG, pagination=pagination)


def _innermost_query(query_node):
    """Test helper: `build_search_query` wraps the base `{"bool": ...}` query
    in a `field_consensus` `function_score` (Task 3) — and optionally a
    `boosting`/another `function_score` on top of that. Peels back those
    wrappers to reach the actual `bool` query node so structural assertions
    written before Task 3 keep checking real behavior."""
    node = query_node
    while isinstance(node, dict):
        if "function_score" in node:
            node = node["function_score"]["query"]
        elif "boosting" in node:
            node = node["boosting"]["positive"]
        else:
            break
    return node


@pytest.fixture(autouse=True)
def _restore_config():
    original = search_service.CONFIG
    yield
    search_service.CONFIG = original


def _mock_post_search(monkeypatch, response=({"hits": {"hits": [], "total": {"value": 0}}}, None)):
    """`search_service._post_search`ü mock'lar ve yapılan çağrıları kaydeder."""
    calls = []

    def fake_post_search(payload, timeout=20, index=None, search_type=None):
        calls.append({"payload": payload, "timeout": timeout, "index": index, "search_type": search_type})
        return response

    monkeypatch.setattr(search_service, "_post_search", fake_post_search)
    return calls


def _hits(n, start=0):
    return [{"_id": f"id{i}", "_score": 1.0, "_source": {"parent_asin": f"A{i}"}} for i in range(start, start + n)]


# ---------------------------------------------------------------------------
# Query: from/size hesaplama
# ---------------------------------------------------------------------------

def test_page_1_produces_from_0():
    payload = search_service.build_search_query("kamera", page=1, apply_intent_reranking=False)
    assert payload["from"] == 0


def test_page_2_produces_from_page_size():
    payload = search_service.build_search_query("kamera", page=2, apply_intent_reranking=False)
    assert payload["from"] == search_service.CONFIG.pagination.page_size


def test_page_3_produces_from_2x_page_size():
    payload = search_service.build_search_query("kamera", page=3, apply_intent_reranking=False)
    assert payload["from"] == 2 * search_service.CONFIG.pagination.page_size


def test_size_comes_from_pagination_page_size():
    payload = search_service.build_search_query("kamera", page=1, apply_intent_reranking=False)
    assert payload["size"] == search_service.CONFIG.pagination.page_size


def test_size_overrides_result_size_when_pagination_enabled():
    # pagination.page_size, limits.result_size'dan farklı bile olsa kazanır —
    # bu yüzden iki alan asla "çelişmez" (bkz. config.PaginationConfig).
    payload = search_service.build_search_query(
        "kamera", page=1, result_size=999, apply_intent_reranking=False
    )
    assert payload["size"] == search_service.CONFIG.pagination.page_size
    assert payload["size"] != 999


def test_page_below_one_normalizes_to_page_1():
    payload_zero = search_service.build_search_query("kamera", page=0, apply_intent_reranking=False)
    payload_negative = search_service.build_search_query("kamera", page=-5, apply_intent_reranking=False)
    assert payload_zero["from"] == 0
    assert payload_negative["from"] == 0


def test_pagination_disabled_omits_from_and_uses_result_size():
    search_service.CONFIG = _with_pagination(enabled=False)
    payload = search_service.build_search_query(
        "kamera", page=3, result_size=42, apply_intent_reranking=False
    )
    assert "from" not in payload
    assert payload["size"] == 42


def test_max_result_window_exceeded_raises_pagination_limit_error():
    search_service.CONFIG = _with_pagination(page_size=20, max_result_window=100)
    # max_allowed_page = 100 // 20 = 5; sayfa 6 -> from=100, from+size=120 > 100
    with pytest.raises(search_service.PaginationLimitError):
        search_service.build_search_query("kamera", page=6, apply_intent_reranking=False)


def test_max_allowed_last_page_does_not_raise():
    search_service.CONFIG = _with_pagination(page_size=20, max_result_window=100)
    # max_allowed_page = 5; from=(5-1)*20=80, from+size=100 == max_result_window (izinli)
    payload = search_service.build_search_query("kamera", page=5, apply_intent_reranking=False)
    assert payload["from"] == 80
    assert payload["size"] == 20


def test_pagination_limit_error_reports_requested_and_max_page():
    search_service.CONFIG = _with_pagination(page_size=20, max_result_window=100)
    with pytest.raises(search_service.PaginationLimitError) as excinfo:
        search_service.build_search_query("kamera", page=99, apply_intent_reranking=False)
    assert excinfo.value.requested_page == 99
    assert excinfo.value.max_allowed_page == 5


def test_autocomplete_query_never_includes_from():
    payload = autocomplete_service.build_autocomplete_query("kam", apply_intent_reranking=False)
    assert "from" not in payload


def test_autocomplete_size_unaffected_by_pagination():
    payload = autocomplete_service.build_autocomplete_query("kam")
    assert payload["size"] == search_service.CONFIG.limits.autocomplete_fetch_size


# ---------------------------------------------------------------------------
# search_products metadata
# ---------------------------------------------------------------------------

def test_total_pages_calculated_correctly(monkeypatch):
    _mock_post_search(
        monkeypatch,
        ({"hits": {"hits": _hits(20), "total": {"value": 45}}}, None),
    )
    result = search_service.search_products("kamera", page=1)
    assert result.total_pages == 3  # ceil(45/20)


def test_start_end_item_correct_for_middle_page(monkeypatch):
    _mock_post_search(
        monkeypatch,
        ({"hits": {"hits": _hits(20), "total": {"value": 45}}}, None),
    )
    result = search_service.search_products("kamera", page=2)
    assert result.start_item == 21
    assert result.end_item == 40


def test_last_page_with_partial_results_has_correct_end_item(monkeypatch):
    # total=45, page_size=20 -> sayfa 3'te yalnızca 5 sonuç var.
    _mock_post_search(
        monkeypatch,
        ({"hits": {"hits": _hits(5), "total": {"value": 45}}}, None),
    )
    result = search_service.search_products("kamera", page=3)
    assert result.start_item == 41
    assert result.end_item == 45
    assert len(result.hits) == 5


def test_search_products_requests_dfs_query_then_fetch_by_default(monkeypatch):
    # Az sayıda shard'da terim istatistikleri shard başına hesaplanır (query_then_fetch),
    # bu da aynı terimin farklı shard'larda çok farklı IDF alıp alakasız sonuçların öne
    # çıkmasına yol açabilir (bkz. CLAUDE.md relevance notları). dfs_query_then_fetch bunu
    # global terim istatistiğiyle düzeltir.
    calls = _mock_post_search(monkeypatch)
    search_service.search_products("kamera", page=1)
    # calls[0] kategori keşfi aggregation isteğidir (search_type hiç geçmez);
    # asıl ürün araması son çağrıdır.
    assert calls[-1]["search_type"] == "dfs_query_then_fetch"


def test_search_products_omits_dfs_query_then_fetch_when_disabled(monkeypatch):
    calls = _mock_post_search(monkeypatch)
    disabled_cfg = dataclasses.replace(
        search_service.CONFIG,
        elasticsearch=dataclasses.replace(
            search_service.CONFIG.elasticsearch, use_dfs_query_then_fetch=False
        ),
    )
    search_service.search_products("kamera", page=1, config=disabled_cfg)
    assert calls[-1]["search_type"] is None


def test_has_previous_and_has_next_first_page(monkeypatch):
    _mock_post_search(
        monkeypatch,
        ({"hits": {"hits": _hits(20), "total": {"value": 45}}}, None),
    )
    result = search_service.search_products("kamera", page=1)
    assert result.has_previous is False
    assert result.has_next is True


def test_has_previous_and_has_next_last_page(monkeypatch):
    _mock_post_search(
        monkeypatch,
        ({"hits": {"hits": _hits(5), "total": {"value": 45}}}, None),
    )
    result = search_service.search_products("kamera", page=3)
    assert result.has_previous is True
    assert result.has_next is False


def test_total_zero_produces_zero_pages_and_no_navigation(monkeypatch):
    _mock_post_search(
        monkeypatch,
        ({"hits": {"hits": [], "total": {"value": 0}}}, None),
    )
    result = search_service.search_products("kamera", page=1)
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
    result = search_service.search_products("kamera", page=2)
    assert result.current_page == 2
    assert result.page_size == search_service.CONFIG.pagination.page_size


def test_total_pages_capped_at_max_allowed_page(monkeypatch):
    search_service.CONFIG = _with_pagination(page_size=20, max_result_window=100)
    # total çok büyük (1_000_000) olsa da max_allowed_page = 5'i aşmamalı.
    _mock_post_search(
        monkeypatch,
        ({"hits": {"hits": _hits(20), "total": {"value": 1_000_000}}}, None),
    )
    result = search_service.search_products("kamera", page=1)
    assert result.total_pages == 5


def test_deep_pagination_beyond_window_returns_friendly_error_not_crash(monkeypatch):
    search_service.CONFIG = _with_pagination(page_size=20, max_result_window=100)
    calls = _mock_post_search(monkeypatch)
    result = search_service.search_products("kamera", page=999)
    assert result.hits is None
    assert result.error  # anlaşılır bir mesaj döner
    # PaginationLimitError, `build_search_query` içinde (from+size hesabı)
    # fırlatılır — bu noktadan SONRA hiçbir ürün arama isteği Elasticsearch'e
    # gitmez. `resolve_intent_signals` (dinamik kategori keşfi) bundan
    # BAĞIMSIZ, `build_search_query`den ÖNCE çalışır; bu yüzden en fazla bir
    # keşif (size=0 aggregation) isteği atılmış olabilir — asla gerçek,
    # aşırı sayfalı bir ürün arama isteği değil.
    assert all(call["payload"].get("size") == 0 for call in calls)


def test_search_error_from_elasticsearch_preserves_pagination_metadata(monkeypatch):
    _mock_post_search(monkeypatch, (None, "bağlantı hatası"))
    result = search_service.search_products("kamera", page=2)
    assert result.hits is None
    assert result.error == "bağlantı hatası"
    assert result.current_page == 2


# ---------------------------------------------------------------------------
# Regresyon: mevcut arama davranışı pagination ile bozulmuyor
# ---------------------------------------------------------------------------

def test_lexical_bool_must_preserved_with_page_param():
    payload = search_service.build_search_query("kamera", page=2, apply_intent_reranking=False)
    must = _innermost_query(payload["query"])["bool"]["must"]
    assert len(must) == 1
    assert "should" in must[0]["bool"]
    assert must[0]["bool"]["minimum_should_match"] == 1


def test_exact_asin_preserved_with_page_param():
    payload = search_service.build_search_query("B000123456", page=2, apply_intent_reranking=False)
    lexical = _innermost_query(payload["query"])["bool"]["must"][0]["bool"]["should"]
    field = search_service.CONFIG.search_methods.exact_asin.field
    # `term` clauses also come from the independent title_ranking exact tier
    # (title.keyword), so filter for the exact-ASIN field specifically
    # rather than assuming it's the only `term` clause present.
    asin_clauses = [c for c in lexical if "term" in c and field in c["term"]]
    assert len(asin_clauses) == 1
    assert asin_clauses[0]["term"][field]["value"] == "B000123456"


def test_turkish_expansion_preserved_with_page_param():
    payload = search_service.build_search_query("kablosuz kulaklık", page=2, apply_intent_reranking=False)
    lexical = _innermost_query(payload["query"])["bool"]["must"][0]["bool"]["should"]
    assert len(lexical) >= 5  # 4 standart lexical + en az 1 çeviri alternatifi


def test_dynamic_category_discovery_preserved_with_pagination(monkeypatch):
    calls = _mock_post_search(
        monkeypatch,
        ({"hits": {"hits": [], "total": {"value": 0}}}, None),
    )
    search_service.search_products("toilet paper", page=2)
    # search_products, resolve_intent_signals üzerinden discovery aggregation
    # isteğini de atar; en az bir çağrı autocomplete index'i DEĞİL, ana
    # index'e (discovery) veya arama index'ine gitmiş olmalı.
    assert calls, "search_products hiç Elasticsearch çağrısı yapmadı"


def test_quality_ranking_stays_disabled_by_default_with_pagination():
    assert search_service.CONFIG.quality_ranking.enabled is False
    # field_consensus (Task 3), popularity_ranking ve accessory_penalty
    # (hepsi varsayılan AÇIK) kendi bağımsız `function_score` sarmalayıcılarını
    # ekliyor; bu doğrulama yalnızca quality_ranking'in kendi wrapper'ına
    # odaklı kalsın diye hepsi burada devre dışı bırakılıyor.
    no_consensus_cfg = dataclasses.replace(
        search_service.CONFIG,
        field_consensus=dataclasses.replace(search_service.CONFIG.field_consensus, enabled=False),
        popularity_ranking=dataclasses.replace(search_service.CONFIG.popularity_ranking, enabled=False),
        accessory_penalty=dataclasses.replace(search_service.CONFIG.accessory_penalty, enabled=False),
    )
    payload = search_service.build_search_query(
        "kamera", page=2, apply_intent_reranking=False, config=no_consensus_cfg
    )
    assert "function_score" not in payload["query"]


def test_discovery_filter_stays_disabled_by_default_with_pagination():
    assert search_service.CONFIG.quality_ranking.discovery_filter_enabled is False


def test_exact_asin_quality_bypass_preserved_with_pagination():
    quality = dataclasses.replace(search_service.CONFIG.quality_ranking, enabled=True, bypass_for_exact_asin=True)
    search_service.CONFIG = dataclasses.replace(
        search_service.CONFIG,
        quality_ranking=quality,
        # popularity_ranking (varsayılan AÇIK) quality_ranking'in etrafına
        # kendi function_score'unu ekleyip "en dıştaki wrapper" varsayımını
        # bozar; bu test yalnızca quality_ranking'in bypass davranışına
        # odaklı olsun diye burada devre dışı bırakılıyor.
        popularity_ranking=dataclasses.replace(search_service.CONFIG.popularity_ranking, enabled=False),
    )
    payload = search_service.build_search_query(
        "B000123456", page=1, enable_exact_asin=True, apply_intent_reranking=False
    )
    functions = payload["query"]["function_score"]["functions"]
    for function in functions:
        filt = function["filter"]
        must_not = filt.get("bool", filt).get("must_not") if isinstance(filt, dict) else None
        assert must_not, f"beklenen bypass must_not bulunamadı: {function}"


def test_exact_asin_page_2_returns_empty_hits_without_crash(monkeypatch):
    # Tek kayıt döndüren bir exact ASIN sorgusunda page=2 istemek (from=20)
    # Elasticsearch'ten boş hits ile total=1 döner; bu çökmemeli, tutarlı
    # şekilde boş sonuç olarak ele alınmalı.
    _mock_post_search(
        monkeypatch,
        ({"hits": {"hits": [], "total": {"value": 1}}}, None),
    )
    result = search_service.search_products("B000123456", page=2)
    assert result.error is None
    assert result.hits == []
    assert result.total == 1
