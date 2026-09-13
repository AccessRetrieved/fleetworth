"""
Phase 4e — knowing its limits.

Per PLAN.md, confidently pricing a blurry photo, a non-truck, or a truck
with missing critical views is a losing outcome. This module gates the
pipeline before pricing runs, and defines the response tiers:

  "priced"          -> normal pricing output
  "not_a_truck"      -> subject isn't a real truck photo, refuse outright
  "needs_more_info"  -> fixable gap (too few usable photos, no tire view,
                        low confidence); names exactly what's needed, per
                        the brief's own "send me a shot of the tires"
                        example

Capture is an unstructured guided session (PLAN.md Phase 2a), not a fixed
named-waypoint checklist — photos aren't labeled by angle, so every check
here is judged from extraction content, never from a slot being empty.
"""
import cv2
import numpy as np

BLUR_VARIANCE_THRESHOLD = 100.0  # Laplacian variance below this = too blurry to trust
NOT_TRUCK_CONFIDENCE_THRESHOLD = 0.3  # extraction confidence below this counts as a "not a truck" vote
LOW_CONFIDENCE_THRESHOLD = 0.35  # fused-pipeline-wide floor before we refuse to price
MIN_USABLE_PHOTOS = 3  # coverage proxy — PLAN.md expects 3-7 photos per session
NEAR_DUPLICATE_HAMMING_THRESHOLD = 12  # out of 64 bits — below this, treat as the same shot repeated
# Raised from 6: real camera photos (auto-exposure/autofocus drift, hand
# shake) vary more between "the same shot twice" than the synthetic test
# used to pick 6 accounted for, so real near-duplicates were slipping
# through uncaught. Still a judgment call pending a real duplicate-photo
# data point — a confirmed genuinely-different photo has scored 20, so
# there's room between 12 and that to tighten further once we see one.


def blur_variance(image_bytes: bytes) -> float:
    """Laplacian variance — low values mean a flat/blurry image."""
    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
    if img is None or img.size == 0:
        return 0.0
    return float(cv2.Laplacian(img, cv2.CV_64F).var())


def is_blurry(image_bytes: bytes, threshold: float = BLUR_VARIANCE_THRESHOLD) -> bool:
    return blur_variance(image_bytes) < threshold


def average_hash(image_bytes: bytes, hash_size: int = 8) -> int | None:
    """8x8 average hash (aHash): shrink to a tiny grayscale thumbnail, then
    each bit is 1 if that pixel is brighter than the thumbnail's mean. Cheap
    and deliberately crude — this isn't meant to recognize the truck, only
    to notice "this is the same shot as one we already have," which the
    photo count alone can't (nothing stops MIN_USABLE_PHOTOS from being hit
    with near-identical frames of one angle)."""
    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
    if img is None or img.size == 0:
        return None
    small = cv2.resize(img, (hash_size, hash_size), interpolation=cv2.INTER_AREA)
    mean = small.mean()
    value = 0
    for bit in (small > mean).flatten():
        value = (value << 1) | int(bit)
    return value


def is_near_duplicate(
    image_bytes: bytes,
    seen_hashes: list[int],
    threshold: int = NEAR_DUPLICATE_HAMMING_THRESHOLD,
) -> bool:
    """True if this photo's average hash is close enough to any
    already-accepted photo's hash that it's effectively a repeat of the
    same view, not a new angle. Photos flagged here don't count toward
    MIN_USABLE_PHOTOS, so 3 near-identical frames of one side can no
    longer pass as adequate coverage."""
    this_hash = average_hash(image_bytes)
    if this_hash is None:
        return False
    return any(bin(this_hash ^ seen).count("1") <= threshold for seen in seen_hashes)


def evaluate(extractions: list[dict | None], duplicate_count: int = 0) -> dict:
    """
    extractions: list of Phase 2b extraction dicts, one per submitted
    photo. A value of None means that photo was missing, too blurry, or a
    near-duplicate of one already counted (caller decides that upstream,
    e.g. via is_blurry()/is_near_duplicate()).
    duplicate_count: how many submitted photos were dropped as
    near-duplicates of another photo already counted — used only to give a
    more specific message when that's why coverage came up short.

    Returns a gating decision dict with a "status" key:
      {"status": "priced"}
      {"status": "not_a_truck", "message": ...}
      {"status": "needs_more_info", "reason": ..., "message": ...}
    """
    usable = [e for e in extractions if e is not None]

    if not usable:
        return {
            "status": "needs_more_info",
            "reason": "no usable images",
            "message": "None of the provided photos were clear enough to assess. "
                       "Please retake in better lighting/focus.",
        }

    # Not-a-truck: majority of usable views agree the subject isn't a real
    # truck photo (wrong subject, or a game/CGI/toy render).
    not_truck_votes = sum(
        1 for e in usable
        if not e.get("is_truck", True)
        or not e.get("is_real_photo", True)
        or e.get("confidence", 1.0) < NOT_TRUCK_CONFIDENCE_THRESHOLD
    )
    if not_truck_votes > len(usable) / 2:
        return {
            "status": "not_a_truck",
            "message": "This doesn't look like a real photo of a truck. "
                       "Please upload actual photos of the truck you'd like priced.",
        }

    # Not enough coverage. We can't check "all sides shown" from content
    # alone, so a minimum usable-photo count is the coverage proxy — but a
    # photo count alone can't stop someone from submitting near-identical
    # shots of one angle, so duplicates are dropped before this check runs
    # and get their own, more specific message.
    if len(usable) < MIN_USABLE_PHOTOS:
        if duplicate_count > 0:
            return {
                "status": "needs_more_info",
                "reason": "duplicate photos",
                "message": f"{duplicate_count} of the submitted photo(s) look like repeats of "
                           f"an angle already captured, leaving only {len(usable)} distinct "
                           f"view(s). Please capture a few different angles of the truck "
                           f"instead of retaking the same shot (at least {MIN_USABLE_PHOTOS} total).",
            }
        return {
            "status": "needs_more_info",
            "reason": f"only {len(usable)} usable photo(s)",
            "message": f"Only {len(usable)} clear photo(s) came through — please add a "
                       f"few more angles of the truck (at least {MIN_USABLE_PHOTOS} total).",
        }

    # Missing critical view: tires. Judged from content (tires_visible),
    # not from a named waypoint slot being empty.
    if not any(e.get("tires_visible") for e in usable):
        return {
            "status": "needs_more_info",
            "reason": "no clear view of tires",
            "message": "Can't assess tire condition — please add a close-up photo of the tires.",
        }

    # Overall low confidence across usable views, even though nothing is
    # individually flagged as not-a-truck or missing.
    avg_conf = sum(e.get("confidence", 0.0) for e in usable) / len(usable)
    if avg_conf < LOW_CONFIDENCE_THRESHOLD:
        return {
            "status": "needs_more_info",
            "reason": "low confidence",
            "message": "The photos aren't clear enough to confidently identify this truck. "
                       "Try retaking in better lighting or from a clearer angle.",
        }

    return {"status": "priced"}
