import pytest

from config import CategoryBoostEntry, CategoryPenaltyEntry, IntentRule
import services.intent_service as intent_service
from services.intent_service import IntentSignals, detect_search_intent, resolve_intent_signals


def _rule(name, **overrides):
    base = dict(
        name=name,
        query_terms=(),
        excluded_when_query_contains=(),
        category_boost_terms=(),
        negative_categories=(),
        category_boost=1,
        category_boost_tr=1,
        label=name,
        icon="",
        priority=0,
        enabled=True,
        all_terms=(),
        any_terms=(),
        excluded_terms=(),
        positive_categories=(),
        soft_negative_categories=(),
    )
    base.update(overrides)
    return IntentRule(**base)


def test_detect_search_intent_single_first_match():
    # "watch" is a whole-word term (no space) so it is matched with word
    # boundaries by _contains_term (identical to search_service.py) — it
    # will not match inside the compound word "smartwatch", so the query
    # text here must be the standalone word itself.
    rules = {"watch": _rule("watch", query_terms=("watch",))}
    info = detect_search_intent("watch", rules)
    assert info["intent"] == "watch"
    assert info["apply_exclusion"] is True


def test_detect_search_intent_no_match_returns_none():
    rules = {"watch": _rule("watch", query_terms=("watch",))}
    info = detect_search_intent("running shoes", rules)
    assert info["intent"] is None
    assert info["rule"] is None


def test_resolve_intent_signals_all_terms_and_match():
    rules = {"iphone_case": _rule(
        "iphone_case",
        all_terms=("iphone", "case"),
        positive_categories=(CategoryBoostEntry(value="Cases", boost=3.0),),
    )}
    signals = resolve_intent_signals("iphone case", [], rules, [])
    assert "iphone_case" in signals.matched_rule_ids
    assert any(c["value"] == "Cases" and c["source"] == "manual" for c in signals.positive_categories)


def test_resolve_intent_signals_all_terms_requires_every_term():
    rules = {"iphone_case": _rule("iphone_case", all_terms=("iphone", "case"))}
    signals = resolve_intent_signals("iphone charger", [], rules, [])
    assert signals.matched_rule_ids == ()


def test_resolve_intent_signals_matches_via_translated_query():
    rules = {"mouse": _rule("mouse", any_terms=("wireless mouse",))}
    signals = resolve_intent_signals("kablosuz fare", ["wireless mouse"], rules, [])
    assert "mouse" in signals.matched_rule_ids


def test_legacy_string_negative_categories_populate_hard_exclusions_only():
    # Same word-boundary reasoning as above: query text must be the
    # standalone term "watch", not the compound word "smartwatch".
    rules = {"watch": _rule("watch", query_terms=("watch",), negative_categories=("books",))}
    signals = resolve_intent_signals("watch", [], rules, [])
    assert signals.negative_categories == ()
    assert len(signals.legacy_hard_exclusions) == 1
    assert signals.legacy_hard_exclusions[0]["bool"]["minimum_should_match"] == 1


def test_object_negative_categories_never_produce_hard_exclusions():
    rules = {"iphone_case": _rule(
        "iphone_case",
        all_terms=("iphone", "case"),
        soft_negative_categories=(CategoryPenaltyEntry(value="Cell Phones", penalty=0.5),),
    )}
    signals = resolve_intent_signals("iphone case", [], rules, [])
    assert signals.legacy_hard_exclusions == ()
    assert signals.negative_categories[0]["value"] == "Cell Phones"
    assert signals.negative_categories[0]["penalty"] == 0.5
    assert signals.negative_categories[0]["source"] == "manual"


def test_negative_penalty_floor_clamps_low_penalty():
    rules = {"x": _rule(
        "x",
        query_terms=("x",),
        soft_negative_categories=(CategoryPenaltyEntry(value="Y", penalty=0.05),),
    )}
    cfg = intent_service.CONFIG
    floor = cfg.intent_ranking.negative_penalty_floor
    signals = resolve_intent_signals("x", [], rules, [])
    assert signals.negative_categories[0]["penalty"] >= floor


def test_legacy_category_boost_terms_carry_distinct_text_and_tr_boosts():
    """Final-review fix (Finding 1): pre-branch behavior emitted TWO
    different boosted clauses per `category_boost_terms` entry —
    `categories_text` at `rule.category_boost` (12 for `watch`) and
    `categories_text.tr` at `rule.category_boost_tr` (8 for `watch`). The
    branch had collapsed this into a single boost value reused on both
    fields, silently changing `watch`'s ranking behavior. `intent_ranking`
    is disabled here to bypass cap scaling and assert the raw per-entry
    values the renderer receives, matching pre-branch behavior exactly."""
    from dataclasses import replace

    rules = {"watch": _rule(
        "watch",
        query_terms=("watch",),
        category_boost_terms=("watches", "smartwatch"),
        category_boost=12,
        category_boost_tr=8,
    )}
    cfg = replace(
        intent_service.CONFIG,
        intent_ranking=replace(intent_service.CONFIG.intent_ranking, enabled=False),
    )
    signals = resolve_intent_signals("watch", [], rules, [], config=cfg)
    manual = [c for c in signals.positive_categories if c["source"] == "manual"]
    assert len(manual) == 2
    for entry in manual:
        assert entry["boost"] == 12
        assert entry["boost_tr"] == 8


def test_new_schema_positive_categories_still_use_single_boost_for_both_fields():
    """Contrast case for Finding 1: the NEW `positive_categories` schema
    field (e.g. `iphone_case`'s `{"value": "Cases", "boost": 3.0}`) must
    remain unchanged — one boost value, no separate `_tr` key, per spec
    §3 ("applied to both fields, no separate _tr key needed")."""
    rules = {"iphone_case": _rule(
        "iphone_case",
        all_terms=("iphone", "case"),
        positive_categories=(CategoryBoostEntry(value="Cases", boost=3.0),),
    )}
    signals = resolve_intent_signals("iphone case", [], rules, [])
    manual = [c for c in signals.positive_categories if c["source"] == "manual"]
    assert len(manual) == 1
    assert manual[0]["boost"] == 3.0
    assert "boost_tr" not in manual[0]


def test_manual_category_boost_cap_scales_down_sum():
    cfg = intent_service.CONFIG
    cap = cfg.intent_ranking.manual_category_boost_cap
    rules = {"x": _rule(
        "x",
        query_terms=("x",),
        positive_categories=(
            CategoryBoostEntry(value="A", boost=cap),
            CategoryBoostEntry(value="B", boost=cap),
        ),
    )}
    signals = resolve_intent_signals("x", [], rules, [])
    manual_total = sum(c["boost"] for c in signals.positive_categories if c["source"] == "manual")
    assert manual_total <= cap + 1e-9


def test_dynamic_category_boost_cap_scales_down_sum():
    cfg = intent_service.CONFIG
    cap = cfg.intent_ranking.dynamic_category_boost_cap
    discovered = [
        {"value": "Electronics", "field": "categories", "doc_count": 10, "rank": 1, "source": "dynamic_category_discovery"},
        {"value": "Computers", "field": "categories", "doc_count": 5, "rank": 2, "source": "dynamic_category_discovery"},
    ]
    signals = resolve_intent_signals("x", [], {}, discovered)
    dynamic_total = sum(c["boost"] for c in signals.positive_categories if c["source"] == "dynamic")
    assert dynamic_total <= cap + 1e-9


def test_dynamic_candidates_same_value_different_field_never_merge():
    discovered = [
        {"value": "Electronics", "field": "categories", "doc_count": 10, "rank": 1, "source": "dynamic_category_discovery"},
        {"value": "Electronics", "field": "main_category", "doc_count": 8, "rank": 1, "source": "dynamic_category_discovery"},
    ]
    signals = resolve_intent_signals("x", [], {}, discovered)
    dynamic_entries = [c for c in signals.positive_categories if c["source"] == "dynamic"]
    assert len(dynamic_entries) == 2
    fields = {c["field"] for c in dynamic_entries}
    assert fields == {"categories", "main_category"}


def test_category_alone_produces_no_lexical_signal():
    # intent_service never returns anything resembling a lexical/must clause —
    # it only ever returns should/must_not/boosting-shaped category signals.
    discovered = [{"value": "X", "field": "categories", "doc_count": 1, "rank": 1, "source": "dynamic_category_discovery"}]
    signals = resolve_intent_signals("anything", [], {}, discovered)
    assert isinstance(signals, IntentSignals)
    assert signals.positive_categories
    # No field named "must" or "lexical" exists anywhere on IntentSignals.
    assert not hasattr(signals, "must")
