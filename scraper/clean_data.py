"""
Phase 1c — clean the raw TruckPaper scrape and build the base-price lookup.

Reads data/truckpaper_raw.jsonl, normalizes make/model naming, drops bad
rows, dedupes, and buckets by (make, model_family, year) -> average price.

Outputs:
  data/truckpaper_clean.jsonl — cleaned per-listing records
  data/base_prices.json       — {make: {model_family: {year: {avg, min, max, count}}}}
"""
import json
import re
from collections import defaultdict
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
RAW_PATH = DATA_DIR / "truckpaper_raw.jsonl"
CLEAN_PATH = DATA_DIR / "truckpaper_clean.jsonl"
BASE_PRICES_PATH = DATA_DIR / "base_prices.json"

# Two-word makes that our "first token = make" title parsing splits wrongly.
MULTI_WORD_MAKES = {
    "AM GENERAL": "AM GENERAL",
    "WESTERN STAR": "WESTERN STAR",
}

# Model-family patterns, checked in order, first match wins. Collapses
# trim/config suffixes (e.g. "CASCADIA 126" -> "CASCADIA") so the pricing
# lookup groups comparable trucks even when a vision-extracted trim guess
# is less specific than the scraped listing title.
MODEL_FAMILY_PATTERNS = [
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


def normalize_make_model(make: str, model: str) -> tuple[str, str]:
    make = (make or "").strip().upper()
    model = (model or "").strip().upper()

    # Fix titles where a two-word make got split into make + model[0].
    combined = f"{make} {model}"
    for prefix, canonical in MULTI_WORD_MAKES.items():
        if combined.startswith(prefix):
            make = canonical
            model = combined[len(prefix):].strip()
            break

    family = model
    for pattern, canonical in MODEL_FAMILY_PATTERNS:
        if pattern.search(model):
            family = canonical
            break

    return make, family


def clean():
    if not RAW_PATH.exists():
        raise SystemExit(f"{RAW_PATH} not found — run scrape_truckpaper.py first")

    seen_ids = set()
    cleaned = []
    dropped = {
        "missing_price": 0, "missing_image": 0, "bad_year": 0, "duplicate": 0,
        "relist": 0, "malformed": 0, "not_a_truck": 0,
    }

    with RAW_PATH.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            # Two machines' overnight auto-pushes can land a commit that
            # snapshots this file mid-write; a torn last line reads as
            # invalid JSON. Skip it rather than crashing the whole merge --
            # the listing gets re-scraped and re-appended cleanly next time.
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                dropped["malformed"] += 1
                continue
            if "listing_id" not in r:
                dropped["malformed"] += 1
                continue

            if r["listing_id"] in seen_ids:
                dropped["duplicate"] += 1
                continue
            seen_ids.add(r["listing_id"])

            # Keyword searches incidentally pull in non-truck inventory
            # (trailers have no engine/cab and share lot listings with
            # trucks; SUVs match generic category keywords; "bodies only"
            # listings are a bare truck bed/box with no chassis) -- none of
            # these are comparable to a photo of a complete truck, and their
            # very different pricing basis would pollute base_prices.json
            # buckets for any make/model overlap.
            category = (r.get("category") or "").lower()
            if "trailer" in category or category == "suv" or "bodies only" in category:
                dropped["not_a_truck"] += 1
                continue

            if not r.get("price") or not r.get("image_urls"):
                dropped["missing_price" if not r.get("price") else "missing_image"] += 1
                continue

            year = r.get("year")
            if not year or not year.isdigit() or not (1980 <= int(year) <= 2027):
                dropped["bad_year"] += 1
                continue

            make, model_family = normalize_make_model(r["make"], r["model"])
            r["make"] = make
            r["model_family"] = model_family
            r["year"] = int(year)
            cleaned.append(r)

    # Relist detection: no VIN is scraped, so fall back to fuzzy matching on
    # (title, price, mileage, location) — but only when mileage is a real
    # nonzero value. Two different used trucks matching on every one of
    # those fields, including the exact odometer reading, is effectively
    # impossible; that combination is the same physical listing under a new
    # listing_id (a relist). Zero/missing mileage is excluded because
    # dealers commonly list several identical *new* trucks (same trim,
    # price, lot) as distinct listings — that's real inventory, not a dupe.
    seen_signatures = set()
    deduped = []
    for r in cleaned:
        mileage = r.get("mileage")
        if mileage:
            signature = (r.get("title"), r.get("price"), mileage, r.get("location"))
            if signature in seen_signatures:
                dropped["relist"] += 1
                continue
            seen_signatures.add(signature)
        deduped.append(r)
    cleaned = deduped

    with CLEAN_PATH.open("w") as f:
        for r in cleaned:
            f.write(json.dumps(r) + "\n")

    # Bucket by (make, model_family, year) -> price stats.
    buckets: dict[str, dict[str, dict[int, list[float]]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for r in cleaned:
        buckets[r["make"]][r["model_family"]][r["year"]].append(r["price"])

    base_prices = {}
    for make, models in buckets.items():
        base_prices[make] = {}
        for model_family, years in models.items():
            base_prices[make][model_family] = {}
            for year, prices in years.items():
                base_prices[make][model_family][str(year)] = {
                    "avg": round(sum(prices) / len(prices), 2),
                    "min": min(prices),
                    "max": max(prices),
                    "count": len(prices),
                }

    with BASE_PRICES_PATH.open("w") as f:
        json.dump(base_prices, f, indent=2, sort_keys=True)

    print(f"Cleaned {len(cleaned)} listings (dropped: {dropped})")
    print(f"Wrote {CLEAN_PATH}")
    n_buckets = sum(len(y) for m in base_prices.values() for y in m.values())
    print(f"Wrote {BASE_PRICES_PATH} — {len(base_prices)} makes, {n_buckets} (make, model, year) buckets")


if __name__ == "__main__":
    clean()
