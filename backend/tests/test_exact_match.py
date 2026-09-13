"""Exact / near-exact image match pricing (exact_match.py): cluster pricing,
multi-view agreement, how compute_price and the pipeline use it, the deeper
search that completes large duplicate clusters, and honest-benchmark leakage
rules. No model or real index needed."""
import numpy as np
import pytest

import pipeline
import pricing_formula
from benchmark_leakage import honest_neighbors
from dino_retrieval import build_index, save_index, search_near_exact
from exact_match import (
    EXACT_MATCH_CONFIDENCE,
    EXACT_MATCH_CONFIDENCE_CAP,
    EXACT_MATCH_CONFLICT_PENALTY,
    EXACT_MATCH_SIMILARITY,
    exact_match_price,
    select_visual,
)
from fusion import fuse_retrieval
from pricing import visual_base_price
from pricing_formula import RANGE_MIN_HALF_WIDTH, compute_price


def neighbor(listing_id, similarity, price=50_000.0, **extra):
    return {"listing_id": listing_id, "similarity": similarity, "price": price, "title": f"comp {listing_id}", **extra}


def lookalikes(n=19, similarity=0.95, start=40_000.0, step=3_000.0):
    return [neighbor(f"n{i}", similarity - 0.001 * i, price=start + step * i) for i in range(n)]


def priced(per_view):
    normal = visual_base_price(fuse_retrieval(per_view), views_queried=len(per_view))
    return normal, select_visual(normal, per_view, views_queried=len(per_view))


EXTRACTION = {
    "make": "FREIGHTLINER",
    "model": "CASCADIA",
    "year_estimate": "2018",
    "condition": "good",
    "tire_condition": "worn",
    "visible_damage": [],
    "confidence": 0.9,
    "views_used": 1,
}


def fake_lookup(price=50_000.0):
    return lambda make, model, year: {
        "base_price": price, "confidence": "high", "match_level": "exact", "sample_size": 3, "notes": "",
    }


# 1. one exact match
def test_one_exact_match_prices_from_that_listing_without_claiming_certainty():
    normal, visual = priced([[neighbor("self", 0.9999, price=83_000.0)] + lookalikes()])

    assert visual["match_regime"] == "near_exact"
    assert visual["visual_base_price"] == 83_000.0
    assert visual["exact_match"]["distinct_listings"] == 1
    assert visual["exact_match"]["conflict"] is False
    assert visual["visual_confidence"] == pytest.approx(EXACT_MATCH_CONFIDENCE)
    assert visual["normal_visual_base_price"] == normal["visual_base_price"]


def test_exact_match_becomes_the_base_price_and_keeps_a_range_floor(monkeypatch):
    monkeypatch.setattr(pricing_formula, "base_price_lookup", fake_lookup(price=50_000.0))
    _, visual = priced([[neighbor("self", 1.0, price=83_000.0)] + lookalikes()])
    result = compute_price(EXTRACTION, visual={"available": True, **visual})

    breakdown = result["breakdown"]
    point = breakdown["internal_point_estimate"]
    low, high = result["price_range"]
    assert breakdown["base_price_source"] == "exact_image_match"
    assert breakdown["base_price"] == 83_000.0
    assert low == pytest.approx(point * (1 - RANGE_MIN_HALF_WIDTH), abs=0.01)
    assert high == pytest.approx(point * (1 + RANGE_MIN_HALF_WIDTH), abs=0.01)
    assert result["confidence"] <= EXACT_MATCH_CONFIDENCE_CAP
    assert any("matches a listing already in our comps data" in note for note in result["notes"])
    assert not any("different price levels" in note for note in result["notes"])  # a disagreeing bucket doesn't cut it


# 2. exact-photo duplicates, same price
def test_duplicate_listings_with_the_same_price_give_that_price():
    view = [neighbor(f"dup{i}", 1.0, price=54_900.0) for i in range(3)] + lookalikes()
    _, visual = priced([view])

    assert visual["visual_base_price"] == 54_900.0
    assert visual["exact_match"]["distinct_listings"] == 3
    assert visual["exact_match"]["distinct_prices"] == 1
    assert visual["price_spread"]["relative_iqr"] == 0
    assert visual["visual_confidence"] == pytest.approx(EXACT_MATCH_CONFIDENCE)


# 3. exact-photo duplicates, different prices
def test_duplicate_listings_with_different_prices_use_their_median_not_top1():
    prices = [30_777.0, 24_777.0, 24_900.0, 26_777.0, 29_777.0]  # FAISS order: top-1 is the priciest copy
    view = [neighbor(f"dup{i}", 1.0 - 1e-5 * i, price=p) for i, p in enumerate(prices)] + lookalikes()
    _, visual = priced([view])

    exact = visual["exact_match"]
    assert visual["visual_base_price"] == 26_777.0
    assert (exact["price_min"], exact["price_max"], exact["distinct_prices"]) == (24_777.0, 30_777.0, 5)
    assert exact["relative_price_range"] == pytest.approx(6_000 / 26_777, abs=1e-3)
    assert visual["visual_confidence"] < EXACT_MATCH_CONFIDENCE


# 4. exact match among many close lookalikes
def test_exact_cluster_dominates_many_close_lookalikes():
    view = [neighbor("self", 0.9995, price=150_000.0)] + lookalikes(similarity=0.975, start=40_000.0, step=1_000.0)
    normal, visual = priced([view])

    assert abs(normal["visual_base_price"] - 150_000.0) / 150_000.0 > 0.2  # the ordinary path dilutes it
    assert visual["visual_base_price"] == 150_000.0
    assert visual["neighbors_used"] == 1


# 5. no exact match: normal pricing unchanged
@pytest.mark.parametrize("view", [
    lookalikes(),
    lookalikes(similarity=EXACT_MATCH_SIMILARITY - 0.0005),  # just under the threshold
    [neighbor("junk", 0.05)],
    [],
])
def test_without_a_near_exact_match_normal_pricing_is_unchanged(view, monkeypatch):
    normal, visual = priced([view])
    assert visual == {**normal, "match_regime": "visual_neighbors", "exact_match": None}

    monkeypatch.setattr(pricing_formula, "base_price_lookup", fake_lookup())
    old = compute_price(EXTRACTION, visual={"available": True, **normal})
    new = compute_price(EXTRACTION, visual={"available": True, **visual})
    assert (new["price_range"], new["confidence"], new["notes"]) == (old["price_range"], old["confidence"], old["notes"])
    assert new["breakdown"]["base_price_source"] == old["breakdown"]["base_price_source"]


def test_pipeline_without_exact_match_matches_the_normal_visual_price(monkeypatch):
    views = [lookalikes(), lookalikes(similarity=0.9)]
    monkeypatch.setattr(pipeline, "retrieve_per_view", lambda photos: [list(v) for v in views])
    visual = pipeline.retrieve_visual_comps([b"front", b"side"], year_estimate="2018")

    normal = visual_base_price(fuse_retrieval(views), views_queried=2, year_estimate="2018")
    assert visual["match_regime"] == "visual_neighbors"
    assert {k: visual[k] for k in normal} == normal


# 6. multi-view agreement / conflict
def test_photos_agreeing_on_the_same_exact_listing_strengthen_confidence():
    one = exact_match_price([[neighbor("A", 1.0, price=60_000.0)] + lookalikes()])
    two = exact_match_price([
        [neighbor("A", 1.0, price=60_000.0)] + lookalikes(),
        [neighbor("A", 0.9993, price=60_000.0)] + lookalikes(),
    ])

    assert two["exact_match"]["agreeing_views"] == 2
    assert two["exact_match"]["conflict"] is False
    assert two["visual_confidence"] > one["visual_confidence"]
    assert two["recurrence_share"] == 1.0
    assert two["visual_base_price"] == 60_000.0


def test_photos_matching_relists_of_the_same_truck_agree():
    truck = {"title": "2019 KENWORTH T680", "mileage": 412_000, "location": "Dallas, Texas"}
    visual = exact_match_price([
        [{**neighbor("A", 1.0, price=70_000.0), **truck}],
        [{**neighbor("B", 1.0, price=70_000.0), **truck}],
    ])
    assert visual["exact_match"]["conflict"] is False
    assert visual["exact_match"]["agreeing_views"] == 2


def test_photos_matching_different_exact_listings_conflict():
    visual = exact_match_price([[neighbor("A", 1.0, price=50_000.0)], [neighbor("B", 1.0, price=120_000.0)]])

    assert visual["exact_match"]["conflict"] is True
    assert visual["exact_match"]["clusters"] == 2
    assert visual["visual_base_price"] == 85_000.0  # pooled median, not whichever photo came first
    assert visual["visual_confidence"] <= EXACT_MATCH_CONFIDENCE * EXACT_MATCH_CONFLICT_PENALTY


def test_conflicting_exact_matches_do_not_override_a_confident_lookup(monkeypatch):
    monkeypatch.setattr(pricing_formula, "base_price_lookup", fake_lookup(price=50_000.0))
    visual = exact_match_price([[neighbor("A", 1.0, price=50_000.0)], [neighbor("B", 1.0, price=120_000.0)]])
    result = compute_price(EXTRACTION, visual={"available": True, **visual})

    assert result["breakdown"]["base_price_source"] == "make_model_year_lookup"
    assert any("different known listings" in note for note in result["notes"])


def test_differently_listed_but_equally_priced_matches_are_consistent():
    visual = exact_match_price([[neighbor("A", 1.0, price=50_000.0)], [neighbor("B", 1.0, price=52_000.0)]])
    assert visual["exact_match"]["conflict"] is False


# cluster completion + honest benchmark leakage
def test_near_exact_search_collects_clusters_larger_than_top_k(tmp_path):
    rng = np.random.default_rng(0)
    photo = rng.normal(size=8).astype(np.float32)
    vectors = np.vstack([np.tile(photo, (30, 1)), rng.normal(size=(50, 8)).astype(np.float32)])
    items = [{"listing_id": str(i), "price": 1_000.0 + i, "title": f"comp {i}"} for i in range(80)]
    save_index(build_index(vectors), items, index_dir=tmp_path)

    found = search_near_exact(photo[None], EXACT_MATCH_SIMILARITY, k=5, index_dir=tmp_path)[0]
    assert sorted(int(n["listing_id"]) for n in found) == list(range(30))


def test_pipeline_completes_a_duplicate_cluster_larger_than_top_k(monkeypatch):
    top_k = [neighbor(f"d{i}", 1.0, price=40_000.0) for i in range(20)]
    cluster = top_k + [neighbor(f"d{i}", 1.0, price=90_000.0) for i in range(20, 50)]
    deeper = []
    monkeypatch.setattr(pipeline, "retrieve_per_view", lambda photos: [list(top_k) for _ in photos])
    monkeypatch.setattr(pipeline, "retrieve_near_exact", lambda photos, threshold: deeper.append(photos) or [list(cluster) for _ in photos])

    visual = pipeline.retrieve_visual_comps([b"front"])
    assert deeper == [[b"front"]]
    assert visual["exact_match"]["distinct_listings"] == 50
    assert visual["visual_base_price"] == 90_000.0


def test_honest_benchmark_removes_every_kind_of_leakage():
    query = {"listing_id": "q", "title": "2018 VOLVO VNL", "price": 50_000.0,
             "image_url": "https://media.sandhills.com/img.axd?id=111&w=350&h=220"}
    candidates = [
        neighbor("q", 1.0),
        neighbor("copy", 0.9995),
        neighbor("same-photo", 0.93, image_url="https://media.sandhills.com/img.axd?id=111&w=1024&h=768"),
        neighbor("relist", 0.92, title="2018 VOLVO VNL", price=50_000.0),
        neighbor("real", 0.91),
    ]
    kept, reasons = honest_neighbors(query, candidates, k=20)

    assert [n["listing_id"] for n in kept] == ["real"]
    assert reasons == {"same_listing": 1, "near_identical": 1, "same_image": 1, "relist_copy": 1}
