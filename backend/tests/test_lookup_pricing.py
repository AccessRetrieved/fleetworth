"""Lookup-table pricing: the bucket base-price rule (mean below
BUCKET_MEDIAN_MIN_LISTINGS listings, median at or above), how the lookup and the
regenerated data/base_prices.json use it, and makes missing from the table,
which fall back to the global average with no bucket sample size -- including
through POST /predict."""
import asyncio
import json
import sys
from pathlib import Path

import pytest

import main
import pipeline
import pricing
from dino_retrieval import RetrievalUnavailable
from exact_match import select_visual
from pricing import BUCKET_MEDIAN_MIN_LISTINGS, aggregate_bucket_prices, base_price_lookup
from pricing_formula import compute_price

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scraper"))
import clean_data  # noqa: E402

UNSEEN_MAKE = "ZZZ-UNSEEN-MAKE"
OUTLIER_BUCKET = [48_000.0, 50_000.0, 52_000.0, 49_000.0, 51_000.0, 3_888_000.0]


@pytest.mark.parametrize("prices, expected", [
    ([40_000.0], 40_000.0),                                            # 1 listing
    ([40_000.0, 50_000.0], 45_000.0),                                  # 2 listings: mean (== median)
    ([40_000.0, 50_000.0, 90_000.0], 60_000.0),                        # 3 listings: mean
    ([40_000.0, 50_000.0, 60_000.0, 130_000.0], 70_000.0),             # 4 listings: mean
    ([40_000.0, 45_000.0, 50_000.0, 55_000.0, 200_000.0], 50_000.0),   # 5 listings: median
    (OUTLIER_BUCKET, 50_500.0),                                        # 6 listings: median
])
def test_bucket_base_price_rule(prices, expected):
    assert aggregate_bucket_prices(prices) == expected
    assert clean_data.bucket_base_price(prices) == expected  # table builder and lookup share the rule


def test_the_median_starts_at_five_listings():
    assert BUCKET_MEDIAN_MIN_LISTINGS == clean_data.BUCKET_MEDIAN_MIN_LISTINGS == 5


def test_extreme_outlier_does_not_distort_a_large_bucket():
    assert sum(OUTLIER_BUCKET) / len(OUTLIER_BUCKET) > 600_000  # what the mean would have said
    assert aggregate_bucket_prices(OUTLIER_BUCKET) == 50_500.0


def test_built_table_adds_the_bucket_price_and_keeps_the_mean():
    listings = [{"make": "KENWORTH", "model_family": "T680", "year": 2019, "price": p} for p in OUTLIER_BUCKET]
    listings.append({"make": "KENWORTH", "model_family": "T680", "year": 2020, "price": 60_000.0})
    table = clean_data.build_base_prices(listings)

    assert table["KENWORTH"]["T680"]["2019"] == {
        "price": 50_500.0, "avg": round(sum(OUTLIER_BUCKET) / 6, 2), "min": 48_000.0, "max": 3_888_000.0, "count": 6,
    }
    assert table["KENWORTH"]["T680"]["2020"] == {"price": 60_000.0, "avg": 60_000.0, "min": 60_000.0, "max": 60_000.0, "count": 1}


def test_every_lookup_level_prices_from_the_bucket_price(monkeypatch):
    monkeypatch.setattr(pricing, "_BASE_PRICES", {"KENWORTH": {"T680": {"2019": {
        "price": 50_500.0, "avg": 690_833.33, "min": 48_000.0, "max": 3_888_000.0, "count": 6,
    }}}})

    levels = {
        "exact": base_price_lookup("Kenworth", "T680", "2019"),
        "nearest_year": base_price_lookup("Kenworth", "T680", "2016"),
        "make_model_all_years": base_price_lookup("Kenworth", "T680", "unknown"),
        "make_only": base_price_lookup("Kenworth", "W900", "2019"),
    }
    for level, lookup in levels.items():
        assert (lookup["match_level"], lookup["base_price"]) == (level, 50_500.0)
    assert levels["exact"]["relative_spread"] == pytest.approx((3_888_000.0 - 48_000.0) / 50_500.0)
    assert base_price_lookup(UNSEEN_MAKE, "ANY", "2019")["base_price"] == 50_500.0  # global average


def test_tables_without_a_bucket_price_still_use_the_mean(monkeypatch):
    monkeypatch.setattr(pricing, "_BASE_PRICES", {"VOLVO": {"VNL": {"2018": {"avg": 42_000.0, "min": 40_000.0, "max": 44_000.0, "count": 2}}}})
    assert base_price_lookup("Volvo", "VNL 760", "2018")["base_price"] == 42_000.0


def test_regenerated_base_prices_follow_the_rule():
    buckets = [b for models in pricing._BASE_PRICES.values() for years in models.values() for b in years.values()]
    assert buckets and all("price" in b for b in buckets)
    for b in buckets:
        if b["count"] < BUCKET_MEDIAN_MIN_LISTINGS:
            assert b["price"] == b["avg"]
        else:
            assert b["min"] <= b["price"] <= b["max"]


EXTRACTION = {
    "make": UNSEEN_MAKE, "model": "PROTOTYPE", "year_estimate": "2018", "condition": "good",
    "tire_condition": "worn", "visible_damage": [], "confidence": 0.9, "views_used": 3,
}


def test_unknown_make_falls_back_to_the_global_average_without_crashing():
    lookup = base_price_lookup(UNSEEN_MAKE, "PROTOTYPE", "2018")
    assert (lookup["match_level"], lookup["sample_size"]) == ("global_fallback", None)

    for visual in (None, {"available": False, "reason": "index not built"}):
        result = compute_price(EXTRACTION, visual=visual)
        breakdown = result["breakdown"]
        assert breakdown["base_price_source"] == "make_model_year_lookup"
        assert breakdown["base_price"] == breakdown["internal_point_estimate"] == lookup["base_price"] > 0
        assert breakdown["base_price_sample_size"] is None
        low, high = result["price_range"]
        assert 0 < low < breakdown["base_price"] < high
        assert 0 < result["confidence"] <= 0.4


def test_unknown_make_with_strong_dino_neighbors_prices_from_them():
    visual = {
        "available": True, "strength": "strong", "visual_base_price": 40_000.0, "visual_confidence": 0.8,
        "price_quantiles": {"q05": 30_000.0, "q95": 55_000.0}, "effective_comps": 10.0,
        "price_spread": {"relative_iqr": 0.2}, "top_similarity": 0.9, "neighbors_used": 20,
    }
    result = compute_price(EXTRACTION, visual=visual)
    assert result["breakdown"]["base_price_source"] == "visual_neighbors"
    assert result["breakdown"]["base_price"] == 40_000.0
    assert result["price_range"] == [30_000.0, 55_000.0]


def test_unknown_make_with_an_exact_image_match_prices_from_that_listing():
    neighbor = {"listing_id": "L1", "similarity": 1.0, "price": 83_000.0, "title": "2018 PROTOTYPE"}
    exact = select_visual({"visual_base_price": None}, [[neighbor]], views_queried=1)
    result = compute_price(EXTRACTION, visual={"available": True, **exact})
    assert result["breakdown"]["base_price_source"] == "exact_image_match"
    assert result["breakdown"]["base_price"] == 83_000.0


async def _post_predict(body: bytes):
    messages, delivered = [], False

    async def receive():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        await asyncio.Event().wait()

    async def send(message):
        messages.append(message)

    await main.app({
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST", "scheme": "http",
        "path": "/predict", "raw_path": b"/predict", "query_string": b"",
        "headers": [(b"content-type", b"multipart/form-data; boundary=test")],
        "client": ("127.0.0.1", 1), "server": ("test", 80),
    }, receive, send)
    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
    return status, json.loads(b"".join(m.get("body", b"") for m in messages))


def test_predict_prices_a_truck_whose_make_is_not_in_the_lookup_table(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(main, "RESULTS_DIR", tmp_path / "results")
    extraction = {**EXTRACTION, "is_truck": True, "is_real_photo": True, "trim": "unknown", "tires_visible": True}
    monkeypatch.setattr(pipeline, "_client", lambda: None)
    monkeypatch.setattr(pipeline, "extract_from_image", lambda *args, **kwargs: dict(extraction))
    monkeypatch.setattr(pipeline, "blur_variance", lambda *args: 1000.0)
    monkeypatch.setattr(pipeline, "is_near_duplicate", lambda *args: False)
    monkeypatch.setattr(pipeline, "average_hash", lambda *args: None)
    monkeypatch.setattr(pipeline, "save_annotated_photos", lambda *args, **kwargs: [])

    def no_index(photos):
        raise RetrievalUnavailable("DINO comp index not found")

    monkeypatch.setattr(pipeline, "retrieve_per_view", no_index)
    photo = b'--test\r\nContent-Disposition: form-data; name="photos"; filename="test"\r\nContent-Type: image/jpeg\r\n\r\nphoto\r\n'
    status, result = asyncio.run(_post_predict(photo * 3 + b"--test--\r\n"))

    assert status == 200
    assert result["status"] == "priced"
    breakdown = result["breakdown"]
    assert (breakdown["base_price_match"], breakdown["base_price_sample_size"]) == ("global_fallback", None)
    assert breakdown["base_price"] == base_price_lookup(UNSEEN_MAKE, "PROTOTYPE", "2018")["base_price"]
