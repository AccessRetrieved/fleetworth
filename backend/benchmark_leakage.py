"""
Leakage rules for honest leave-one-out DINO pricing benchmarks.

Production pricing may recognize a photo that is already in the comp index
(exact_match.py). A benchmark that queries the index with its own comps must
not let that happen, or it measures memorization instead of pricing. A
neighbor is leakage for a query comp when it is:

  same_listing    the same listing_id
  same_image      the same photo: same URL (media id, ignoring size params) or
                  the same image_sha256 when the metadata records one
  near_identical  cosine >= EXACT_MATCH_SIMILARITY -- identical bytes always
                  land here, as do near-identical copies of the photo
  relist_copy     the same title and price (relists and duplicate listings of
                  the same truck, even with a different photo)
"""
from collections import Counter
from urllib.parse import parse_qs, urlsplit

from exact_match import EXACT_MATCH_SIMILARITY


def image_key(url: str | None) -> str | None:
    """Photo identity regardless of the size parameters build_dino_index
    rewrites: sandhills media URLs name the photo with id=."""
    if not url:
        return None
    parts = urlsplit(url)
    photo_id = parse_qs(parts.query).get("id")
    return f"{parts.netloc.lower()}{parts.path.lower()}?id={photo_id[0]}" if photo_id else url


def leakage_reason(query: dict, neighbor: dict, similarity: float, threshold: float = EXACT_MATCH_SIMILARITY) -> str | None:
    if neighbor.get("listing_id") == query.get("listing_id"):
        return "same_listing"
    photo = image_key(query.get("image_url"))
    if (photo and image_key(neighbor.get("image_url")) == photo) or (
        query.get("image_sha256") and neighbor.get("image_sha256") == query.get("image_sha256")
    ):
        return "same_image"
    if similarity >= threshold:
        return "near_identical"
    if query.get("title") and (neighbor.get("title"), neighbor.get("price")) == (query.get("title"), query.get("price")):
        return "relist_copy"
    return None


def honest_neighbors(query: dict, candidates: list[dict], k: int,
                     threshold: float = EXACT_MATCH_SIMILARITY) -> tuple[list[dict], Counter]:
    """The first k candidates (search output, most similar first) that aren't
    leakage for `query`, and how many of each kind were skipped on the way."""
    kept, reasons = [], Counter()
    for candidate in candidates:
        reason = leakage_reason(query, candidate, float(candidate.get("similarity", 0.0)), threshold)
        if reason:
            reasons[reason] += 1
            continue
        kept.append(candidate)
        if len(kept) == k:
            break
    return kept, reasons
