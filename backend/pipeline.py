"""
Full backend pipeline: waypoint images -> extraction -> limits gate ->
fusion -> pricing. This is what Phase 5's /predict endpoint will call.
"""
from fusion import fuse_extractions
from limits import evaluate, is_blurry
from pricing_formula import compute_price
from vision_extract import _client, extract_from_image


def run_pipeline(waypoint_images: dict[str, str | bytes]) -> dict:
    """
    waypoint_images: {waypoint_name: image (URL, path, or raw bytes)}.
    Waypoint names should match limits.REQUIRED_WAYPOINTS where possible;
    unrecognized keys are still extracted and fused, just not counted
    against the required-views check.

    Returns one of:
      {"status": "priced", "price_estimate": ..., "price_range": [...], "breakdown": {...}}
      {"status": "not_a_truck", "message": ...}
      {"status": "needs_more_info", "reason": ..., "message": ...}
    """
    client = _client()

    extractions: dict[str, dict | None] = {}
    for waypoint, image in waypoint_images.items():
        if isinstance(image, bytes) and is_blurry(image):
            extractions[waypoint] = None
            continue
        extractions[waypoint] = extract_from_image(image, client=client)

    gate = evaluate(extractions)
    if gate["status"] != "priced":
        return gate

    usable = [e for e in extractions.values() if e is not None]
    fused = fuse_extractions(usable)
    return {"status": "priced", **compute_price(fused)}
