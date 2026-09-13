"""run_pipeline wiring for the DINO signal, with the OpenAI and DINO calls
stubbed out."""
import pytest

import pipeline
from dino_retrieval import RetrievalUnavailable

EXTRACTION = {
    "is_truck": True,
    "is_real_photo": True,
    "make": "FREIGHTLINER",
    "model": "CASCADIA 126",
    "year_estimate": "2018",
    "trim": "unknown",
    "condition": "good",
    "visible_damage": [],
    "tires_visible": True,
    "tire_condition": "worn",
    "confidence": 0.9,
}


@pytest.fixture
def stub_vlm(monkeypatch):
    monkeypatch.setattr(pipeline, "_client", lambda: None)
    monkeypatch.setattr(pipeline, "extract_from_image", lambda photo, client=None: dict(EXTRACTION))
    monkeypatch.setattr(pipeline, "blur_variance", lambda photo: 0.0 if photo == b"blurry" else 1000.0)
    monkeypatch.setattr(pipeline, "is_near_duplicate", lambda photo, seen_hashes: False)


def test_priced_response_carries_visual_comps(stub_vlm, monkeypatch):
    queried = []

    def fake_retrieve(photos):
        queried.extend(photos)
        return [
            [{"listing_id": f"L{i}", "similarity": 0.8 - 0.01 * i, "price": 45_000.0, "title": "comp"} for i in range(8)]
            for _ in photos
        ]

    monkeypatch.setattr(pipeline, "retrieve_per_view", fake_retrieve)
    result = pipeline.run_pipeline([b"front", b"blurry", b"side", b"rear"])

    assert result["status"] == "priced"
    assert queried == [b"front", b"side", b"rear"]  # the blurry photo never reaches retrieval
    visual = result["breakdown"]["visual_comps"]
    assert visual["available"] is True
    assert visual["views_queried"] == 3
    assert visual["strength"] == "strong"
    assert visual["visual_base_price"] == 45_000.0
    assert visual["recurrence_share"] == 1.0
    assert visual["per_view"] == [{"top_similarity": 0.8, "mean_similarity": 0.765}] * 3
    for key in ("top_similarity", "mean_similarity", "price_spread", "neighbors_used", "signal_agreement"):
        assert key in visual


def test_missing_index_still_prices_from_vlm_path(stub_vlm, monkeypatch):
    def not_built(photos):
        raise RetrievalUnavailable("DINO comp index not found")

    monkeypatch.setattr(pipeline, "retrieve_per_view", not_built)
    result = pipeline.run_pipeline([b"front", b"side", b"rear"])

    assert result["status"] == "priced"
    assert result["breakdown"]["base_price_source"] == "make_model_year_lookup"
    visual = result["breakdown"]["visual_comps"]
    assert visual["available"] is False
    assert "not found" in visual["reason"]


def test_unexpected_retrieval_error_does_not_fail_the_request(stub_vlm, monkeypatch):
    def broken(photos):
        raise OSError("cannot identify image file")

    monkeypatch.setattr(pipeline, "retrieve_per_view", broken)
    result = pipeline.run_pipeline([b"front", b"side", b"rear"])

    assert result["status"] == "priced"
    assert result["breakdown"]["visual_comps"]["reason"].startswith("OSError")
