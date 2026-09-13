"""
Exact / near-exact image match pricing.

A submitted photo that is already in the comp index -- or a copy of one --
comes back from DINO at cosine ~1.0. The normal visual price
(fusion.fuse_retrieval -> pricing.visual_base_price) deliberately blends that
match with the ~0.90-0.97 lookalikes around it, which is right for a truck the
index has never seen but dilutes near-certain evidence here. This module
detects that regime and prices from the near-exact cluster alone:

  - every neighbor at >= EXACT_MATCH_SIMILARITY, in any view, joins the
    cluster, counted once per listing: dealers post one photo on several
    listings and relists reuse photos, so a "match" is often several rows
  - price = weighted median of the cluster's distinct listing prices, each
    weighted by how many query photos matched it -- never simply whichever
    FAISS row came first
  - confidence starts at EXACT_MATCH_CONFIDENCE (not 1.0), shrinks with the
    cluster's price spread, grows a little per agreeing photo, and is capped;
    pricing_formula still keeps its range floor
  - photos whose clusters share no listing or vehicle and disagree on price
    are a conflict: the price pools every cluster and confidence is cut

With no near-exact match, select_visual returns the normal visual result
unchanged (plus match_regime="visual_neighbors").

Production may use this; honest benchmarks must not. A benchmark comp's own
near-exact copies are exactly the leakage benchmark_leakage.py removes.
"""
from pricing import VISUAL_PRICE_QUANTILES, VISUAL_TOP_COMPS_SHOWN, _weighted_quantile

# Same photo, or a copy of it. Identical bytes score 1.0 and float16-stored
# index vectors ~0.99999; genuinely different photos of look-alike trucks top
# out around 0.97-0.98 on the comp index. Resized/recompressed copies land
# around 0.99-0.999, so part of them fall below this (see eval_exact_match.py).
EXACT_MATCH_SIMILARITY = 0.999
EXACT_MATCH_MAX_CLUSTER = 500  # deepest search used to complete one photo's duplicate cluster
EXACT_MATCH_CONFIDENCE = 0.9  # one uncontested near-exact listing: strong, but never certainty
EXACT_MATCH_CONFIDENCE_CAP = 0.95
EXACT_MATCH_VIEW_BONUS = 0.05  # per extra query photo agreeing on the same cluster
EXACT_MATCH_CONFLICT_PENALTY = 0.5  # photos matching different, differently priced clusters
# Clusters sharing no listing still describe consistent evidence when their
# prices agree this closely (e.g. identical fleet trucks listed separately).
EXACT_MATCH_PRICE_AGREEMENT = 1.10
EXACT_MATCH_LISTINGS_REPORTED = 50


def _vehicle_key(item: dict) -> tuple | None:
    """Relist signature (scraper/clean_data.py): same title, price, mileage and
    location. Only with a real mileage -- identical new trucks aren't relists."""
    mileage = item.get("mileage")
    return (item.get("title"), item.get("price"), mileage, item.get("location")) if mileage else None


def near_exact_by_view(per_view_neighbors: list[list[dict]], threshold: float = EXACT_MATCH_SIMILARITY) -> list[dict[str, dict]]:
    """For each query photo, its priced near-exact neighbors keyed by
    listing_id (highest similarity kept)."""
    views = []
    for neighbors in per_view_neighbors:
        matches: dict[str, dict] = {}
        for neighbor in neighbors:
            listing_id = neighbor.get("listing_id")
            similarity = float(neighbor.get("similarity", 0.0))
            if listing_id is None or similarity < threshold or not (neighbor.get("price") or 0) > 0:
                continue
            if listing_id not in matches or similarity > matches[listing_id]["similarity"]:
                matches[listing_id] = neighbor
        views.append(matches)
    return views


def cluster_is_truncated(neighbors: list[dict], threshold: float = EXACT_MATCH_SIMILARITY) -> bool:
    """Every returned neighbor is near-exact, so the duplicate cluster may
    continue past the search depth and needs a deeper search."""
    return bool(neighbors) and float(neighbors[-1].get("similarity", 0.0)) >= threshold


def _median_price(listings: dict[str, dict]) -> float:
    prices = [float(n["price"]) for n in listings.values()]
    return _weighted_quantile(prices, [1.0] * len(prices), 0.5)


def _same_vehicle(a: dict, b: dict) -> bool:
    if a["listings"].keys() & b["listings"].keys():
        return True
    keys_a = {_vehicle_key(n) for n in a["listings"].values()} - {None}
    keys_b = {_vehicle_key(n) for n in b["listings"].values()} - {None}
    if keys_a & keys_b:
        return True
    price_a, price_b = _median_price(a["listings"]), _median_price(b["listings"])
    return max(price_a, price_b) / min(price_a, price_b) <= EXACT_MATCH_PRICE_AGREEMENT


def _group_views(views: list[dict[str, dict]]) -> list[dict]:
    """Query photos grouped into near-exact clusters that describe the same
    vehicle / consistent price evidence."""
    groups = [{"views": [i], "listings": dict(matches)} for i, matches in enumerate(views) if matches]
    merged = True
    while merged:
        merged = False
        for a in range(len(groups)):
            for b in range(a + 1, len(groups)):
                if _same_vehicle(groups[a], groups[b]):
                    groups[a]["views"] += groups[b]["views"]
                    groups[a]["listings"].update(groups[b]["listings"])
                    del groups[b]
                    merged = True
                    break
            if merged:
                break
    return groups


def exact_match_price(per_view_neighbors: list[list[dict]], views_queried: int | None = None,
                      threshold: float = EXACT_MATCH_SIMILARITY) -> dict | None:
    """Visual-price result (same keys as pricing.visual_base_price, plus
    match_regime and exact_match) priced from the near-exact cluster only, or
    None when no query photo has a near-exact match."""
    views = near_exact_by_view(per_view_neighbors, threshold)
    groups = _group_views(views)
    if not groups:
        return None
    views_queried = len(per_view_neighbors) if views_queried is None else views_queried

    # Pooled evidence: each near-exact listing once, weighted by how many photos matched it.
    weight: dict[str, int] = {}
    best: dict[str, dict] = {}
    for matches in views:
        for listing_id, neighbor in matches.items():
            weight[listing_id] = weight.get(listing_id, 0) + 1
            if listing_id not in best or neighbor["similarity"] > best[listing_id]["similarity"]:
                best[listing_id] = neighbor
    listing_ids = sorted(best, key=lambda l: (-weight[l], -float(best[l]["similarity"]), str(l)))
    prices = [float(best[l]["price"]) for l in listing_ids]
    weights = [float(weight[l]) for l in listing_ids]
    total_weight = sum(weights)

    median = _weighted_quantile(prices, weights, 0.5)
    p25 = _weighted_quantile(prices, weights, 0.25)
    p75 = _weighted_quantile(prices, weights, 0.75)
    relative_iqr = (p75 - p25) / median
    similarities = [float(best[l]["similarity"]) for l in listing_ids]
    conflict = len(groups) > 1
    agreeing_views = max(len(g["views"]) for g in groups)

    confidence = EXACT_MATCH_CONFIDENCE / (1 + relative_iqr) * (1 + EXACT_MATCH_VIEW_BONUS * (agreeing_views - 1))
    confidence = min(EXACT_MATCH_CONFIDENCE_CAP, confidence)
    if conflict:
        confidence *= EXACT_MATCH_CONFLICT_PENALTY

    return {
        "match_regime": "near_exact",
        "strength": "strong",
        "visual_base_price": round(median, 2),
        "weighted_mean_price": round(sum(p * w for p, w in zip(prices, weights)) / total_weight, 2),
        "price_spread": {"p25": round(p25, 2), "p75": round(p75, 2), "relative_iqr": round(relative_iqr, 3)},
        "price_quantiles": {
            f"q{round(q * 100):02d}": round(_weighted_quantile(prices, weights, q), 2) for q in VISUAL_PRICE_QUANTILES
        },
        "effective_comps": round(total_weight ** 2 / sum(w * w for w in weights), 2),
        "top_similarity": round(max(similarities), 4),
        "mean_similarity": round(sum(similarities) / len(similarities), 4),
        "neighbors_used": len(listing_ids),
        "recurrence_share": None if views_queried < 2 else round(sum(w for w in weights if w >= 2) / total_weight, 3),
        # The matched listing's price is already for its own model year, so no year shift applies.
        "query_year": None,
        "year_adjusted": False,
        "median_year_gap": None,
        "visual_confidence": round(confidence, 3),
        "top_comps": [
            {
                "listing_id": l,
                "title": best[l].get("title"),
                "year": best[l].get("year"),
                "price": best[l]["price"],
                "adjusted_price": None,
                "similarity": round(float(best[l]["similarity"]), 4),
                "views_matched": weight[l],
                "detail_url": best[l].get("detail_url"),
            }
            for l in listing_ids[:VISUAL_TOP_COMPS_SHOWN]
        ],
        "exact_match": {
            "threshold": threshold,
            "distinct_listings": len(listing_ids),
            "distinct_prices": len(set(prices)),
            "price_min": min(prices),
            "price_max": max(prices),
            "relative_price_range": round((max(prices) - min(prices)) / median, 3),
            "views_queried": views_queried,
            "views_matched": sum(1 for matches in views if matches),
            "agreeing_views": agreeing_views,
            "clusters": len(groups),
            "conflict": conflict,
            "listing_ids": listing_ids[:EXACT_MATCH_LISTINGS_REPORTED],
        },
    }


def select_visual(normal: dict, per_view_neighbors: list[list[dict]], views_queried: int | None = None,
                  threshold: float = EXACT_MATCH_SIMILARITY) -> dict:
    """The visual signal pricing should use: the near-exact cluster price when
    one exists, otherwise `normal` (pricing.visual_base_price output) as-is."""
    exact = exact_match_price(per_view_neighbors, views_queried, threshold)
    if exact is None:
        return {**normal, "match_regime": "visual_neighbors", "exact_match": None}
    return {**exact, "normal_visual_base_price": normal.get("visual_base_price")}
