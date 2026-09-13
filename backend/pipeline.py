"""
Full backend pipeline: photos -> extraction -> damage visualization ->
limits gate -> fusion -> pricing. This is what Phase 5's /predict
endpoint will call.

Input is a plain, unlabeled list of photos from one guided capture
session (PLAN.md Phase 2a) — not named waypoints. A video may also be
submitted alongside these photos, but it is never passed to this
pipeline; it's stored as-is by the API layer as an authenticity record.
"""
from damage_visualization import save_annotated_photos
from fusion import fuse_extractions
from limits import BLUR_VARIANCE_THRESHOLD, average_hash, blur_variance, evaluate, is_near_duplicate
from pricing_formula import compute_price
from vision_extract import _client, extract_from_image


def run_pipeline(
    photos: list[str | bytes],
    submission_id: str | None = None,
) -> dict:
    """
    photos: list of images (URL, path, or raw bytes) — the auto-snapped
    exterior photos from one guided capture session.
    submission_id: when given, triggers Phase 2c — any photo with
    localized damage gets an annotated copy saved to
    results/<submission_id>/, and a priced result's breakdown includes
    "annotated_damage_photos": a list of URL paths (served by main.py's
    /results static mount) the frontend can render directly.

    Returns one of:
      {"status": "priced", "price_range": [...], "confidence": ..., "notes": [...], "breakdown": {...}}
      {"status": "not_a_truck", "message": ...}
      {"status": "needs_more_info", "reason": ..., "message": ...}
    """
    client = _client()

    extractions: list[dict | None] = []
    seen_hashes: list[int] = []
    duplicate_count = 0
    for photo in photos:
        if isinstance(photo, bytes):
            variance = blur_variance(photo)
            if variance < BLUR_VARIANCE_THRESHOLD:
                print(f"[blur-check] REJECTED as blurry, variance={variance:.1f} (threshold {BLUR_VARIANCE_THRESHOLD})", flush=True)
                extractions.append(None)
                continue
            print(f"[blur-check] accepted, variance={variance:.1f}", flush=True)
            # Reject near-identical repeats of a view already counted —
            # otherwise MIN_USABLE_PHOTOS can be satisfied with the same
            # shot taken 3 times instead of real coverage.
            photo_hash = average_hash(photo)
            if photo_hash is not None and seen_hashes:
                closest = min(bin(photo_hash ^ h).count("1") for h in seen_hashes)
                print(f"[dup-check] closest Hamming distance to an accepted photo: {closest}", flush=True)
            if is_near_duplicate(photo, seen_hashes):
                duplicate_count += 1
                extractions.append(None)
                continue
            if photo_hash is not None:
                seen_hashes.append(photo_hash)
        extractions.append(extract_from_image(photo, client=client))

    annotated_paths = []
    if submission_id is not None:
        annotated_paths = save_annotated_photos(submission_id, photos, extractions)

    gate = evaluate(extractions, duplicate_count=duplicate_count)
    if gate["status"] != "priced":
        return gate

    usable = [e for e in extractions if e is not None]
    fused = fuse_extractions(usable)
    result = {"status": "priced", **compute_price(fused)}
    result["breakdown"]["annotated_damage_photos"] = [
        f"/results/{submission_id}/{path.name}" for path in annotated_paths
    ]
    return result
