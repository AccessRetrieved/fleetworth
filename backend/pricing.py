"""
Phase 4a — base price lookup.

Loads data/base_prices.json (built by scraper/clean_data.py, bucketed by
make -> model_family -> year -> {avg, min, max, count}) and exposes
base_price_lookup(make, model, year) -> result dict.

Fallback order, per PLAN.md Phase 4d:
  1. Exact (make, model_family, year) bucket
  2. Nearest year within the same (make, model_family)
  3. (make, model_family) averaged across all years
  4. (make) averaged across all models/years
  5. Global average across the whole dataset ("generic truck"), low confidence
"""
import json
import re
from pathlib import Path

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


def _global_average() -> float:
    all_prices = []
    for models in _BASE_PRICES.values():
        for years in models.values():
            for bucket in years.values():
                all_prices.append(bucket["avg"] * bucket["count"])
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
                "base_price": year_bucket["avg"],
                "confidence": "high",
                "match_level": "exact",
                "sample_size": year_bucket["count"],
                "notes": f"Exact match: {make_key} {model_family} {year_int}",
            }

    # 2. Nearest year within same (make, model_family)
    if make_buckets and model_family in make_buckets and year_int is not None:
        years_available = make_buckets[model_family]
        nearest_year = min(years_available.keys(), key=lambda y: abs(int(y) - year_int))
        nearest_bucket = years_available[nearest_year]
        return {
            "base_price": nearest_bucket["avg"],
            "confidence": "medium",
            "match_level": "nearest_year",
            "sample_size": nearest_bucket["count"],
            "notes": f"No {year_int} comps for {make_key} {model_family}; used nearest year {nearest_year}",
        }

    # 3. (make, model_family) averaged across all years
    if make_buckets and model_family in make_buckets:
        years_available = make_buckets[model_family]
        prices = [b["avg"] * b["count"] for b in years_available.values()]
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
                prices.append(b["avg"] * b["count"])
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
