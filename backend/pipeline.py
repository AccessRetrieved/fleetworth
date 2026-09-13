"""
Full backend pipeline: photos -> extraction -> damage visualization ->
limits gate -> fusion -> pricing. This is what Phase 5's /predict
endpoint will call.

DINOv2 visual retrieval runs alongside the VLM path on the same usable
photos (embed -> Top-K comps per view -> multi-view retrieval fusion ->
visual base price), giving pricing a second, non-generative base-price
signal to fall back on and cross-check against (PLAN.md architecture).

Input is a plain, unlabeled list of photos from one guided capture
session (PLAN.md Phase 2a) — not named waypoints. A video may also be
submitted alongside these photos, but it is never passed to this
pipeline; it's stored as-is by the API layer as an authenticity record.
"""
from damage_visualization import save_annotated_photos
from dino_retrieval import METHOD as VISUAL_METHOD
from dino_retrieval import RetrievalUnavailable, retrieve_per_view
from fusion import fuse_extractions, fuse_retrieval
from limits import BLUR_VARIANCE_THRESHOLD, average_hash, blur_variance, evaluate, is_near_duplicate
from pricing import visual_base_price
from pricing_formula import compute_price
from vision_extract import _client, extract_from_image


def retrieve_visual_comps(photos: list[str | bytes], year_estimate=None) -> dict:
    """DINO signal for a set of usable photos: {method, available, ...
    visual_base_price stats}. year_estimate is the fused VLM year — DINOv2
    can't see model year, so comp prices are shifted to it when it's known.
    Never raises — if the index hasn't been built or retrieval fails, pricing
    continues on the VLM/lookup path alone and the breakdown says why."""
    try:
        per_view = retrieve_per_view(photos)
    except RetrievalUnavailable as e:
        return {"method": VISUAL_METHOD, "available": False, "reason": str(e)}
    except Exception as e:  # model load, image decode, etc. — shouldn't sink a priceable request
        return {"method": VISUAL_METHOD, "available": False, "reason": f"{type(e).__name__}: {e}"}

    # Per-view diagnostics (PLAN.md 2b-DINO): a single view with no close
    # comps is visible here even when other views carry the fused score.
    per_view_stats = [
        {
            "top_similarity": round(neighbors[0]["similarity"], 4) if neighbors else None,
            "mean_similarity": round(sum(n["similarity"] for n in neighbors) / len(neighbors), 4) if neighbors else None,
        }
        for neighbors in per_view
    ]
    fused = fuse_retrieval(per_view)
    return {
        "method": VISUAL_METHOD,
        "available": True,
        "views_queried": len(per_view),
        "per_view": per_view_stats,
        **visual_base_price(fused, views_queried=len(per_view), year_estimate=year_estimate),
    }


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
            if blur_variance(photo) < BLUR_VARIANCE_THRESHOLD:
                extractions.append(None)
                continue
            # Reject near-identical repeats of a view already counted —
            # otherwise MIN_USABLE_PHOTOS can be satisfied with the same
            # shot taken 3 times instead of real coverage.
            if is_near_duplicate(photo, seen_hashes):
                duplicate_count += 1
                extractions.append(None)
                continue
            photo_hash = average_hash(photo)
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
    usable_photos = [p for p, e in zip(photos, extractions) if e is not None]
    fused = fuse_extractions(usable)
    visual = retrieve_visual_comps(usable_photos, year_estimate=fused.get("year_estimate"))
    result = {"status": "priced", **compute_price(fused, visual=visual)}
    result["breakdown"]["annotated_damage_photos"] = [
        f"/results/{submission_id}/{path.name}" for path in annotated_paths
    ]
    return result
