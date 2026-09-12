"""
Full backend pipeline: photos -> extraction -> limits gate -> fusion ->
pricing. This is what Phase 5's /predict endpoint will call.

Input is a plain, unlabeled list of photos from one guided capture
session (PLAN.md Phase 2a) — not named waypoints. A video may also be
submitted alongside these photos, but it is never passed to this
pipeline; it's stored as-is by the API layer as an authenticity record.
"""
from fusion import fuse_extractions
from limits import evaluate, is_blurry
from pricing_formula import compute_price
from vision_extract import _client, extract_from_image


def run_pipeline(photos: list[str | bytes]) -> dict:
    """
    photos: list of images (URL, path, or raw bytes), one per
    auto-snapped photo from the capture session.

    Returns one of:
      {"status": "priced", "price_range": [...], "confidence": ..., "breakdown": {...}}
      {"status": "not_a_truck", "message": ...}
      {"status": "needs_more_info", "reason": ..., "message": ...}
    """
    client = _client()

    extractions: list[dict | None] = []
    for photo in photos:
        if isinstance(photo, bytes) and is_blurry(photo):
            extractions.append(None)
            continue
        extractions.append(extract_from_image(photo, client=client))

    gate = evaluate(extractions)
    if gate["status"] != "priced":
        return gate

    usable = [e for e in extractions if e is not None]
    fused = fuse_extractions(usable)
    return {"status": "priced", **compute_price(fused)}
