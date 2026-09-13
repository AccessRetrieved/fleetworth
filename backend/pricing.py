"""
Phase 4a — base price lookup.

Loads data/base_prices.json (built by scraper/clean_data.py, bucketed by
make -> model_family -> year -> {price, avg, min, max, count}, where price is
the bucket base price: median at BUCKET_MEDIAN_MIN_LISTINGS+ listings, else mean) and exposes
base_price_lookup(make, model, year) -> result dict.

Fallback order, per PLAN.md Phase 4d:
  1. Exact (make, model_family, year) bucket
  2. Nearest year within the same (make, model_family)
  3. (make, model_family) averaged across all years
  4. (make) averaged across all models/years
  5. Global average across the whole dataset ("generic truck"), low confidence

Also exposes visual_base_price(fused_comps) — the second base-price signal,
computed from DINO-retrieved visually similar comps instead of an
identified make/model/year (PLAN.md Phase 4b).
"""
import json
import math
import re
import statistics
from pathlib import Path

from fusion import RETRIEVAL_MIN_SIMILARITY

BASE_PRICES_PATH = Path(__file__).resolve().parent.parent / "data" / "base_prices.json"

with BASE_PRICES_PATH.open() as f:
    _BASE_PRICES: dict = json.load(f)

# Mirror scraper/clean_data.py's model-family collapsing so a vision-extracted
# model string (e.g. "Cascadia 126") maps onto the same bucket keys used when
# building base_prices.json (e.g. "CASCADIA").
_MODEL_FAMILY_PATTERNS = [
    (re.compile(r"CASCADIA"), "CASCADIA"),
    (re.compile(r"\bVNL\w*"), "VNL"),
    (re.compile(r"BUSINESS CLASS M2|(?<![A-Z])M2\b"), "M2"),
    (re.compile(r"\bT680\b"), "T680"),
    (re.compile(r"\bT880\b"), "T880"),
    (re.compile(r"\bT370\b"), "T370"),
    (re.compile(r"\b389\b"), "389"),
    (re.compile(r"DURASTAR"), "DURASTAR"),
    (re.compile(r"^LT\b"), "LT"),
    (re.compile(r"\bNPR\w*"), "NPR"),
    (re.compile(r"STAR 47X"), "47X"),
    (re.compile(r"STAR 4700"), "4700"),
]

# DINO visual-neighborhood pricing (cosine similarities, see fusion.py).
VISUAL_STRONG_TOP_SIMILARITY = 0.75  # best match clearly above a random truck pair (~0.68) = trustworthy neighborhood
VISUAL_MIN_NEIGHBORS = 5  # fewer usable comps than this can't form a robust price distribution
VISUAL_TOP_COMPS_SHOWN = 5  # closest comps echoed back in the breakdown for explainability
# Weighted quantiles of the comp-price distribution, reported alongside the
# median. pricing_formula derives the displayed price range from a pair of them.
VISUAL_PRICE_QUANTILES = (0.05, 0.10, 0.15, 0.20, 0.80, 0.85, 0.90, 0.95)

# Lookup bucket base price. The plain mean is pulled up by occasional extreme
# listings (confident-identity held-out median predicted/real 1.036). The median
# fixes that where a bucket has enough listings; 3-4 listing buckets did better
# with the mean, and 1-2 listing buckets are identical either way
# (eval_lookup_aggregation.py: median predicted/real 1.004, MdAPE 22.2% -> 21.4%,
# no loss in +/-20/30% accuracy or range coverage).
BUCKET_MEDIAN_MIN_LISTINGS = 5

# Year-aware visual pricing. DINOv2 sees a 2008 and a 2018 truck of the same
# body style as near-identical, but they're priced a decade apart.
DEPRECIATION_PER_YEAR = 0.088  # fitted log(price) vs model-year slope over the 375 comps
YEAR_WEIGHT_SCALE = 2.0  # a comp this many years outside the estimated year range keeps 1/e of its weight


def _normalize_model_family(model: str) -> str:
    model = (model or "").strip().upper()
    for pattern, canonical in _MODEL_FAMILY_PATTERNS:
        if pattern.search(model):
            return canonical
    return model


def _parse_year(year) -> int | None:
    if year is None:
        return None
    m = re.search(r"\d{4}", str(year))
    return int(m.group()) if m else None


def parse_year_estimate(year_estimate) -> tuple[float, float] | None:
    """VLM year_estimate -> (center year, half-width of the range).
    "2019" -> (2019, 0), "2018-2020" -> (2019, 1), "unknown" -> None."""
    years = [int(y) for y in re.findall(r"\b(?:19|20)\d{2}\b", str(year_estimate or ""))]
    if not years:
        return None
    return (min(years) + max(years)) / 2, (max(years) - min(years)) / 2


def aggregate_bucket_prices(prices: list[float]) -> float:
    """A lookup bucket's base price: the median once the bucket has
    BUCKET_MEDIAN_MIN_LISTINGS listings, otherwise the mean. Mirrors
    scraper/clean_data.bucket_base_price, which writes it into base_prices.json."""
    prices = [float(p) for p in prices]
    if len(prices) >= BUCKET_MEDIAN_MIN_LISTINGS:
        return round(statistics.median(prices), 2)
    return round(sum(prices) / len(prices), 2)


def _bucket_price(bucket: dict) -> float:
    """The bucket's base price; tables built before "price" existed carry only the mean."""
    return bucket.get("price", bucket["avg"])


def _global_average() -> float:
    all_prices = []
    for models in _BASE_PRICES.values():
        for years in models.values():
            for bucket in years.values():
                all_prices.append(_bucket_price(bucket) * bucket["count"])
    total_count = sum(
        bucket["count"]
        for models in _BASE_PRICES.values()
        for years in models.values()
        for bucket in years.values()
    )
    return sum(all_prices) / total_count if total_count else 0.0


def base_price_lookup(make: str, model: str, year) -> dict:
    """Returns {base_price, confidence, match_level, sample_size, notes}."""
    make_key = (make or "").strip().upper()
    model_family = _normalize_model_family(model)
    year_int = _parse_year(year)

    make_buckets = _BASE_PRICES.get(make_key)

    # 1. Exact (make, model_family, year)
    if make_buckets and model_family in make_buckets and year_int is not None:
        year_bucket = make_buckets[model_family].get(str(year_int))
        if year_bucket:
            return {
                "base_price": _bucket_price(year_bucket),
                "confidence": "high",
                "match_level": "exact",
                "sample_size": year_bucket["count"],
                "relative_spread": (year_bucket["max"] - year_bucket["min"]) / max(_bucket_price(year_bucket), 1),
                "notes": f"Exact match: {make_key} {model_family} {year_int}",
            }

    # 2. Nearest year within same (make, model_family)
    if make_buckets and model_family in make_buckets and year_int is not None:
        years_available = make_buckets[model_family]
        nearest_year = min(years_available.keys(), key=lambda y: abs(int(y) - year_int))
        nearest_bucket = years_available[nearest_year]
        return {
            "base_price": _bucket_price(nearest_bucket),
            "confidence": "medium",
            "match_level": "nearest_year",
            "sample_size": nearest_bucket["count"],
            "relative_spread": (nearest_bucket["max"] - nearest_bucket["min"]) / max(_bucket_price(nearest_bucket), 1),
            "notes": f"No {year_int} comps for {make_key} {model_family}; used nearest year {nearest_year}",
        }

    # 3. (make, model_family) averaged across all years
    if make_buckets and model_family in make_buckets:
        years_available = make_buckets[model_family]
        prices = [_bucket_price(b) * b["count"] for b in years_available.values()]
        counts = [b["count"] for b in years_available.values()]
        avg = sum(prices) / sum(counts)
        return {
            "base_price": round(avg, 2),
            "confidence": "medium",
            "match_level": "make_model_all_years",
            "sample_size": sum(counts),
            "notes": f"No year match for {make_key} {model_family}; averaged across all years",
        }

    # 4. (make) averaged across all models/years
    if make_buckets:
        prices, counts = [], []
        for years in make_buckets.values():
            for b in years.values():
                prices.append(_bucket_price(b) * b["count"])
                counts.append(b["count"])
        avg = sum(prices) / sum(counts)
        return {
            "base_price": round(avg, 2),
            "confidence": "low",
            "match_level": "make_only",
            "sample_size": sum(counts),
            "notes": f"No model match for {model_family} under {make_key}; averaged across all {make_key} listings",
        }

    # 5. Global fallback — unrecognized make entirely
    return {
        "base_price": round(_global_average(), 2),
        "confidence": "low",
        "match_level": "global_fallback",
        "sample_size": None,
        "notes": f"Unrecognized make {make!r}; used global average across all scraped listings",
    }


def _weighted_quantile(values: list[float], weights: list[float], q: float) -> float:
    """Weighted quantile with linear interpolation between the two comps that
    bracket the target cumulative weight (each comp's weighted "slot" is
    centered at the midpoint of its cumulative-weight interval).

    Plain "smallest value whose cumulative weight reaches q" snaps to actual
    comp prices, which is coarse with only ~5-20 comps and especially with
    RETRIEVAL_ALPHA this sharp (score mass concentrates on 1-2 comps, so the
    crossing point can land awkwardly on the far side of a comp's slot).
    Interpolating removes that quantization noise: measured 27.9% -> 27.1%
    median leave-one-out error on the comp index (build_dino_index.py
    --sanity-only), with no change to accuracy within 10%/20% of true price.
    """
    pairs = sorted(zip(values, weights))
    total = sum(w for _, w in pairs)
    if total <= 0:
        return pairs[-1][0]
    target = q * total
    cumulative = 0.0
    midpoints = []
    for value, weight in pairs:
        midpoints.append((value, cumulative + weight / 2))
        cumulative += weight

    if target <= midpoints[0][1]:
        return midpoints[0][0]
    if target >= midpoints[-1][1]:
        return midpoints[-1][0]
    for (v0, m0), (v1, m1) in zip(midpoints, midpoints[1:]):
        if m0 <= target <= m1:
            frac = (target - m0) / (m1 - m0) if m1 > m0 else 0.0
            return v0 + frac * (v1 - v0)
    return midpoints[-1][0]


def _year_adjusted(comp: dict, query_year: float, half_width: float) -> dict:
    """Shift a comp's listed price to the query truck's model year with the
    dataset-wide depreciation rate, and down-weight comps from years outside
    the estimated range. Comps without a year pass through unchanged."""
    if comp.get("year") is None:
        return comp
    gap = query_year - float(comp["year"])
    years_outside_range = max(0.0, abs(gap) - half_width)
    return {
        **comp,
        "listed_price": comp["price"],
        "price": comp["price"] * (1 + DEPRECIATION_PER_YEAR) ** gap,
        "score": comp["score"] * math.exp(-years_outside_range / YEAR_WEIGHT_SCALE),
    }


def visual_base_price(fused_comps: list[dict], views_queried: int = 1, year_estimate=None) -> dict:
    """
    Robust visual base price from DINO-retrieved comps (fusion.fuse_retrieval
    output). Deliberately NOT the nearest neighbor's price: every comp's real
    listed price is weighted by its fused retrieval score (similarity**alpha
    summed across views, with a recurrence bonus) and the weighted *median*
    is used, so one oddly priced lookalike can't drag the estimate.

    year_estimate: the VLM's fused year_estimate. When parseable, each comp's
    price is first shifted to that model year (DEPRECIATION_PER_YEAR) and
    comps from far-off years are down-weighted (YEAR_WEIGHT_SCALE) — DINOv2
    can't tell model years apart, but price depends heavily on them.

    Returns:
      strength            "strong" | "weak" | "none" (no comp cleared the similarity floor)
      visual_base_price   similarity-weighted median comp price
      weighted_mean_price for comparison with the median
      price_spread        {p25, p75, relative_iqr} — weighted IQR / median, an uncertainty signal
      price_quantiles     {q05, q10, ..., q95} — weighted comp-price quantiles (same weights as the median)
      effective_comps     Kish effective number of comps behind those weights
      top_similarity, mean_similarity, neighbors_used
      recurrence_share    share of weight from comps retrieved by 2+ views (None for one view)
      query_year, year_adjusted, median_year_gap
      visual_confidence   0-1, see the terms below
      top_comps           closest comps, for the explanation
    """
    comps = [c for c in fused_comps if (c.get("price") or 0) > 0 and c.get("score", 0) > 0]
    query_year = parse_year_estimate(year_estimate)
    year_adjusted = query_year is not None and any(c.get("year") is not None for c in comps)
    if year_adjusted:
        comps = sorted((_year_adjusted(c, *query_year) for c in comps), key=lambda c: c["score"], reverse=True)

    if not comps:
        return {
            "strength": "none",
            "visual_base_price": None,
            "weighted_mean_price": None,
            "price_spread": None,
            "price_quantiles": None,
            "effective_comps": 0.0,
            "top_similarity": None,
            "mean_similarity": None,
            "neighbors_used": 0,
            "recurrence_share": None,
            "query_year": None if query_year is None else query_year[0],
            "year_adjusted": False,
            "median_year_gap": None,
            "visual_confidence": 0.0,
            "top_comps": [],
        }

    prices = [float(c["price"]) for c in comps]
    weights = [float(c["score"]) for c in comps]
    total_weight = sum(weights)

    median = _weighted_quantile(prices, weights, 0.5)
    p25 = _weighted_quantile(prices, weights, 0.25)
    p75 = _weighted_quantile(prices, weights, 0.75)
    relative_iqr = (p75 - p25) / median
    weighted_mean = sum(p * w for p, w in zip(prices, weights)) / total_weight
    price_quantiles = {
        f"q{round(q * 100):02d}": round(_weighted_quantile(prices, weights, q), 2) for q in VISUAL_PRICE_QUANTILES
    }
    # Kish effective sample size: how many comps the weights really rest on.
    # With the sharp RETRIEVAL_ALPHA one clearly-closer comp can carry nearly
    # all the weight, collapsing every quantile onto its single price.
    effective_comps = total_weight ** 2 / sum(w * w for w in weights)

    similarities = [float(c["best_similarity"]) for c in comps]
    top_similarity = max(similarities)
    mean_similarity = sum(similarities) / len(similarities)

    recurrence_share = None
    if views_queried >= 2:
        recurring = sum(w for c, w in zip(comps, weights) if c.get("views_matched", 1) >= 2)
        recurrence_share = recurring / total_weight

    median_year_gap = None
    if year_adjusted:
        dated = [(abs(query_year[0] - c["year"]), w) for c, w in zip(comps, weights) if c.get("year") is not None]
        median_year_gap = _weighted_quantile([gap for gap, _ in dated], [w for _, w in dated], 0.5)

    # Each term is 0-1 and kept simple enough to explain from the breakdown:
    # how close the best match is, how much the neighbors agree on price,
    # whether there are enough of them, and whether views agree on them.
    similarity_term = min(1.0, max(0.0, (top_similarity - RETRIEVAL_MIN_SIMILARITY)
                                   / (VISUAL_STRONG_TOP_SIMILARITY - RETRIEVAL_MIN_SIMILARITY)))
    spread_term = 1 / (1 + relative_iqr)
    count_term = min(1.0, len(comps) / VISUAL_MIN_NEIGHBORS)
    consistency_term = 1.0 if recurrence_share is None else 0.8 + 0.2 * recurrence_share
    visual_confidence = similarity_term * spread_term * count_term * consistency_term

    strong = top_similarity >= VISUAL_STRONG_TOP_SIMILARITY and len(comps) >= VISUAL_MIN_NEIGHBORS
    return {
        "strength": "strong" if strong else "weak",
        "visual_base_price": round(median, 2),
        "weighted_mean_price": round(weighted_mean, 2),
        "price_spread": {"p25": round(p25, 2), "p75": round(p75, 2), "relative_iqr": round(relative_iqr, 3)},
        "price_quantiles": price_quantiles,
        "effective_comps": round(effective_comps, 2),
        "top_similarity": round(top_similarity, 4),
        "mean_similarity": round(mean_similarity, 4),
        "neighbors_used": len(comps),
        "recurrence_share": None if recurrence_share is None else round(recurrence_share, 3),
        "query_year": None if query_year is None else query_year[0],
        "year_adjusted": year_adjusted,
        "median_year_gap": median_year_gap,
        "visual_confidence": round(visual_confidence, 3),
        "top_comps": [
            {
                "listing_id": c["listing_id"],
                "title": c.get("title"),
                "year": c.get("year"),
                "price": c.get("listed_price", c["price"]),
                "adjusted_price": round(c["price"], 2) if "listed_price" in c else None,
                "similarity": c["best_similarity"],
                "views_matched": c.get("views_matched", 1),
                "detail_url": c.get("detail_url"),
            }
            for c in comps[:VISUAL_TOP_COMPS_SHOWN]
        ],
    }
