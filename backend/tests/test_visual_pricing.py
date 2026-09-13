"""DINO retrieval fusion, visual base pricing, and how compute_price combines
the visual signal with the make/model/year lookup. Pure Python — no model
or index needed."""
import pytest

import pricing_formula
from fusion import fuse_retrieval
from pricing import _weighted_quantile, visual_base_price
from pricing_formula import VISUAL_ONLY_CONFIDENCE_CAP, compute_price


def neighbor(listing_id, similarity, price=50_000.0):
    return {"listing_id": listing_id, "similarity": similarity, "price": price, "title": f"comp {listing_id}"}


def test_listing_recurring_across_views_outranks_single_view_match():
    fused = fuse_retrieval([
        [neighbor("B", 0.900), neighbor("A", 0.895)],
        [neighbor("A", 0.890), neighbor("C", 0.50)],
    ])

    assert [c["listing_id"] for c in fused][:2] == ["A", "B"]
    assert fused[0]["views_matched"] == 2
    assert fused[0]["best_similarity"] == 0.895
    assert fused[0]["mean_similarity"] == pytest.approx(0.8925)
    assert "similarity" not in fused[0]


def test_weak_matches_are_dropped():
    fused = fuse_retrieval([[neighbor("A", 0.9), neighbor("junk", 0.05)]])
    assert [c["listing_id"] for c in fused] == ["A"]


def test_same_listing_twice_in_one_view_counts_once():
    fused = fuse_retrieval([[neighbor("A", 0.6), neighbor("A", 0.9)]])
    assert fused[0]["views_matched"] == 1
    assert fused[0]["best_similarity"] == 0.9


def test_weighted_quantile():
    assert _weighted_quantile([10, 20, 30], [1, 1, 1], 0.5) == 20
    # Interpolated: 30 carries most of the weight (10 of 12) so the median
    # lands close to it, but not exactly on it — pure "snap to the value that
    # crosses the target" quantization is what interpolation is fixing here.
    assert _weighted_quantile([10, 20, 30], [1, 1, 10], 0.5) == pytest.approx(28.18, abs=0.01)


def test_visual_price_is_not_the_nearest_neighbors_price():
    # The outlier is only marginally closer than the pack. (With the sharp
    # RETRIEVAL_ALPHA, a comp that is *clearly* closer does dominate by design.)
    view = [neighbor("outlier", 0.855, price=400_000.0)] + [
        neighbor(f"n{i}", 0.85 - 0.01 * i, price=50_000.0 + 2_000 * i) for i in range(6)
    ]
    visual = visual_base_price(fuse_retrieval([view]))

    assert visual["strength"] == "strong"
    assert 50_000 <= visual["visual_base_price"] <= 60_000
    assert visual["top_similarity"] == 0.855
    assert 0 < visual["mean_similarity"] < visual["top_similarity"]
    assert visual["neighbors_used"] == 7
    assert visual["price_spread"]["p25"] <= visual["visual_base_price"] <= visual["price_spread"]["p75"]
    assert 0 < visual["visual_confidence"] <= 1
    assert visual["top_comps"][0]["listing_id"] == "outlier"


def test_recurrence_share_only_reported_for_multiple_views():
    views = [[neighbor(f"n{i}", 0.8) for i in range(5)]] * 2
    assert visual_base_price(fuse_retrieval(views), views_queried=2)["recurrence_share"] == 1.0
    assert visual_base_price(fuse_retrieval(views[:1]), views_queried=1)["recurrence_share"] is None


def test_no_usable_neighbors_reports_none():
    visual = visual_base_price(fuse_retrieval([[neighbor("junk", 0.05)]]))
    assert visual["strength"] == "none"
    assert visual["visual_base_price"] is None
    assert visual["visual_confidence"] == 0.0


def test_few_or_low_similarity_neighbors_are_weak():
    visual = visual_base_price(fuse_retrieval([[neighbor("A", 0.62), neighbor("B", 0.55)]]))
    assert visual["strength"] == "weak"
    assert visual["neighbors_used"] == 2


EXTRACTION = {
    "make": "FREIGHTLINER",
    "model": "CASCADIA",
    "year_estimate": "2018",
    "condition": "good",
    "tire_condition": "worn",
    "visible_damage": [],
    "confidence": 0.9,
    "views_used": 4,
}


def fake_lookup(match_level="exact", confidence="high", price=50_000.0):
    return lambda make, model, year: {
        "base_price": price,
        "confidence": confidence,
        "match_level": match_level,
        "sample_size": 3,
        "notes": "",
    }


def strong_visual(price, confidence=0.85):
    return {
        "method": "test",
        "available": True,
        "strength": "strong",
        "visual_base_price": price,
        "visual_confidence": confidence,
        "top_similarity": 0.8,
        "neighbors_used": 20,
    }


def range_width(result):
    low, high = result["price_range"]
    return high - low


def test_without_visual_signal_prices_from_lookup(monkeypatch):
    monkeypatch.setattr(pricing_formula, "base_price_lookup", fake_lookup(confidence="medium"))
    result = compute_price(EXTRACTION)

    assert result["breakdown"]["base_price_source"] == "make_model_year_lookup"
    assert result["breakdown"]["base_price"] == 50_000.0
    assert 0.5 < result["confidence"] < 0.7  # three comps limit support
    assert result["breakdown"]["visual_comps"]["available"] is False


def test_agreeing_visual_comps_raise_confidence(monkeypatch):
    monkeypatch.setattr(pricing_formula, "base_price_lookup", fake_lookup(confidence="medium"))
    baseline = compute_price(EXTRACTION)
    result = compute_price(EXTRACTION, visual=strong_visual(52_000.0))

    assert result["breakdown"]["visual_comps"]["signal_agreement"] == "agree"
    assert result["breakdown"]["base_price"] == 50_000.0
    assert result["confidence"] > baseline["confidence"]
    assert range_width(result) < range_width(baseline)


def test_strong_disagreement_lowers_confidence_and_widens_range(monkeypatch):
    monkeypatch.setattr(pricing_formula, "base_price_lookup", fake_lookup(confidence="high"))
    baseline = compute_price(EXTRACTION)
    result = compute_price(EXTRACTION, visual=strong_visual(120_000.0))

    assert result["breakdown"]["visual_comps"]["signal_agreement"] == "disagree"
    assert result["breakdown"]["base_price_source"] == "make_model_year_lookup"
    assert result["confidence"] < baseline["confidence"]
    assert range_width(result) > range_width(baseline)
    assert any("different price levels" in note for note in result["notes"])


@pytest.mark.parametrize(
    "match_level, match_confidence, vlm_confidence",
    [("global_fallback", "low", 0.9), ("make_only", "low", 0.9), ("exact", "high", 0.4)],
)
def test_uncertain_identity_falls_back_to_visual_base_price(monkeypatch, match_level, match_confidence, vlm_confidence):
    monkeypatch.setattr(pricing_formula, "base_price_lookup", fake_lookup(match_level, match_confidence, price=90_000.0))
    result = compute_price({**EXTRACTION, "confidence": vlm_confidence}, visual=strong_visual(40_000.0))

    assert result["breakdown"]["base_price_source"] == "visual_neighbors"
    assert result["breakdown"]["base_price"] == 40_000.0
    assert result["breakdown"]["lookup_base_price"] == 90_000.0
    assert result["confidence"] <= VISUAL_ONLY_CONFIDENCE_CAP


def test_uncertain_identity_with_weak_visual_keeps_lookup(monkeypatch):
    monkeypatch.setattr(pricing_formula, "base_price_lookup", fake_lookup("global_fallback", "low", price=90_000.0))
    weak = {**strong_visual(40_000.0), "strength": "weak"}
    result = compute_price({**EXTRACTION, "make": "unknown"}, visual=weak)

    assert result["breakdown"]["base_price_source"] == "make_model_year_lookup"
    assert result["breakdown"]["base_price"] == 90_000.0
    assert result["breakdown"]["visual_comps"]["signal_agreement"] is None


def test_single_comp_has_lower_confidence_and_wider_range(monkeypatch):
    base = fake_lookup()(None, None, None)
    monkeypatch.setattr(pricing_formula, "base_price_lookup", lambda *args: {**base, "sample_size": 1})
    thin = compute_price(EXTRACTION)
    monkeypatch.setattr(pricing_formula, "base_price_lookup", lambda *args: {**base, "sample_size": 12})
    supported = compute_price(EXTRACTION)
    assert thin["confidence"] <= 0.5
    assert thin["confidence"] < supported["confidence"]
    assert range_width(thin) > range_width(supported)


@pytest.mark.parametrize("strength", ["strong", "weak"])
def test_visual_spread_widens_range_even_when_medians_agree(monkeypatch, strength):
    monkeypatch.setattr(pricing_formula, "base_price_lookup", fake_lookup())
    visual = {**strong_visual(50_000), "strength": strength}
    narrow = compute_price(EXTRACTION, visual={**visual, "price_spread": {"relative_iqr": 0}})
    broad = compute_price(EXTRACTION, visual={**visual, "price_spread": {"relative_iqr": 1.8}})
    assert broad["confidence"] < narrow["confidence"]
    assert range_width(broad) > range_width(narrow)


def test_lookup_spread_widens_range(monkeypatch):
    base = {**fake_lookup()(None, None, None), "sample_size": 12}
    monkeypatch.setattr(pricing_formula, "base_price_lookup", lambda *args: base)
    narrow = compute_price(EXTRACTION)
    base["relative_spread"] = 1.5
    broad = compute_price(EXTRACTION)
    assert range_width(broad) > range_width(narrow)
