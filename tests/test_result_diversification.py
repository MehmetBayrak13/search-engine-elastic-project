from dataclasses import replace

from services import autocomplete_service, search_service

from services.search_service import diversify_hits


def _hit(store, title):
    return {"_source": {"store": store, "title": title}}


def test_diversify_pushes_second_variant_of_same_family_to_the_end():
    hits = [
        _hit("Mixiba", "Mixiba Bluetooth Headband Headphones for Women Sport Pink"),
        _hit("Mixiba", "Mixiba Bluetooth Headband Headphones for Women Sport Blue"),
        _hit("Avantree", "AVANTREE Audition PRO Low Latency Wireless Headphones"),
        _hit("JBL", "JBL Tune 510BT Wireless On-Ear Headphones"),
    ]
    result = diversify_hits(hits, search_service.CONFIG)
    titles = [h["_source"]["title"] for h in result]
    assert titles == [
        "Mixiba Bluetooth Headband Headphones for Women Sport Pink",
        "AVANTREE Audition PRO Low Latency Wireless Headphones",
        "JBL Tune 510BT Wireless On-Ear Headphones",
        "Mixiba Bluetooth Headband Headphones for Women Sport Blue",
    ]


def test_diversify_never_changes_result_count():
    hits = [_hit("A", "Same Product Title One"), _hit("A", "Same Product Title Two"),
            _hit("A", "Same Product Title Three"), _hit("B", "Different Product")]
    result = diversify_hits(hits, search_service.CONFIG)
    assert len(result) == len(hits)
    assert sorted(id(h) for h in result) == sorted(id(h) for h in hits)


def test_diversify_leaves_already_distinct_results_untouched():
    hits = [_hit("A", "Wireless Mouse Gaming"), _hit("B", "Mechanical Keyboard RGB"),
            _hit("C", "USB C Hub Adapter")]
    result = diversify_hits(hits, search_service.CONFIG)
    assert result == hits


def test_diversify_handles_missing_store_or_title_without_crashing():
    hits = [{"_source": {}}, {"_source": {"title": "Only Title"}}, {"_source": {"store": "OnlyStore"}}]
    result = diversify_hits(hits, search_service.CONFIG)
    assert len(result) == 3


def test_diversify_disabled_via_config_preserves_original_order():
    off_cfg = replace(search_service.CONFIG, result_diversification=replace(search_service.CONFIG.result_diversification, enabled=False))
    hits = [
        _hit("Mixiba", "Mixiba Bluetooth Headband Headphones for Women Sport Pink"),
        _hit("Mixiba", "Mixiba Bluetooth Headband Headphones for Women Sport Blue"),
    ]
    result = diversify_hits(hits, off_cfg)
    assert result == hits


def test_diversify_single_hit_is_a_noop():
    hits = [_hit("A", "Solo Product")]
    assert diversify_hits(hits, search_service.CONFIG) == hits


def test_diversify_empty_list_is_a_noop():
    assert diversify_hits([], search_service.CONFIG) == []


def test_diversify_ignores_common_stopwords_when_building_family_key():
    # "for"/"with" gibi yapısal kelimeler farklı ürünleri yanlışlıkla
    # aynı aileye sokmamalı -- ama aynı kelimeler farklı bir ürünün
    # sırasını da bozmamalı. Burada iki AYRI ürün "for"/"the" içeriyor
    # ama anlamlı kelimeleri farklı, gruplanmamalı.
    hits = [
        _hit("Acme", "Case for the New Phone Model X"),
        _hit("Acme", "Charger for the New Tablet Model Y"),
    ]
    result = diversify_hits(hits, search_service.CONFIG)
    assert result == hits


def test_search_products_diversification_never_changes_total_or_page_size(monkeypatch):
    from services import search_service
    from services.search_models import SearchResult

    hits_payload = [
        {"_id": f"B{i}", "_score": 10 - i, "_source": {"store": "Mixiba", "title": f"Mixiba Widget Variant {i}"}}
        for i in range(5)
    ]

    def fake_post_search(payload, timeout=20, index=None, search_type=None):
        if payload.get("size") == 0:
            return {"aggregations": {}}, None
        return {"hits": {"hits": hits_payload, "total": {"value": 123}}}, None

    monkeypatch.setattr(search_service, "_post_search", fake_post_search)
    result: SearchResult = search_service.search_products("widget")
    assert result.total == 123
    assert len(result.hits) == len(hits_payload)
