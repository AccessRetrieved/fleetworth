"""Compare vision_extract.py accuracy across VLM models (e.g. gpt-4o-mini
vs gpt-4o) against real scraped TruckPaper listings with known ground truth.

Checks the three fields pricing actually depends on:
  - make match
  - model_family match (via pricing._normalize_model_family, so e.g.
    "CASCADIA 126" and "Cascadia" both collapse to "CASCADIA")
  - year_estimate within +/-1 year of the true listing year (year_estimate
    may be a single year or a range like "2018-2020" -- counted correct if
    the true year falls in [min-1, max+1])

Usage:
    uv run python eval_vlm_accuracy.py --model gpt-4o-mini --samples 40
    uv run python eval_vlm_accuracy.py --model gpt-4o --samples 40
Use the same --samples count (and the fixed seed below) across runs to
compare like-for-like on the identical sample of trucks.
"""
import argparse
import json
import random
import re
import time
from pathlib import Path

import vision_extract
from pricing import _normalize_model_family
from vision_extract import _client, extract_from_image

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "truckpaper_clean.jsonl"
SEED = 42


def upsize(url: str) -> str:
    """Scraped image_urls are small listing-grid thumbnails; request a
    bigger, uncropped version from the same media service for extraction
    accuracy (mirrors test_extraction.py)."""
    return url.replace("w=250&h=220", "w=1024&h=768").replace("sz=Cover", "sz=Max")


def year_matches(year_estimate: str, true_year: int) -> bool:
    years = [int(y) for y in re.findall(r"\b(?:19|20)\d{2}\b", str(year_estimate or ""))]
    if not years:
        return False
    return (min(years) - 1) <= true_year <= (max(years) + 1)


def extract_with_retry(image_url: str, client, max_retries: int = 6) -> dict:
    """extract_from_image already swallows API errors into a
    {"_error": ...} fallback dict rather than raising, so a plain
    try/except here can't see rate limits -- retry at this level instead,
    backing off whenever the returned _error names a RateLimitError, up to
    max_retries times before giving up and returning that last result."""
    delay = 1.0
    for attempt in range(max_retries + 1):
        result = extract_from_image(image_url, client=client)
        if not (result.get("_error") or "").startswith("RateLimitError"):
            return result
        if attempt == max_retries:
            return result
        time.sleep(delay)
        delay = min(delay * 2, 15.0)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default=vision_extract.MODEL, help="OpenAI vision model to use (default: vision_extract.MODEL)")
    parser.add_argument("--samples", type=int, default=40, help="number of listings to sample (default 40)")
    parser.add_argument("--verbose", action="store_true", help="print each extraction, not just the summary")
    args = parser.parse_args()

    vision_extract.MODEL = args.model  # override for this run only

    rows = [json.loads(l) for l in DATA_PATH.open() if l.strip()]
    rows = [
        r for r in rows
        if r.get("image_urls") and r.get("make") and r.get("model")
        # a handful of scraped listings' first image is a broken/placeholder
        # icon (e.g. sandhills' "no-image-icon.svg") rather than a real photo
        and "no-image-icon" not in r["image_urls"][0]
    ]
    sample = random.Random(SEED).sample(rows, min(args.samples, len(rows)))

    client = _client()
    make_correct = model_family_correct = year_correct = 0
    errors = 0
    start = time.monotonic()

    for i, r in enumerate(sample, 1):
        true_make = r["make"].strip().upper()
        true_family = _normalize_model_family(r["model"])
        true_year = r.get("year")
        image_url = upsize(r["image_urls"][0])

        result = extract_with_retry(image_url, client=client)
        if i > 1:
            time.sleep(0.3)  # light pacing to avoid tripping the TPM rate limit in the first place
        if result.get("_error"):
            errors += 1

        got_make = (result.get("make") or "").strip().upper()
        got_family = _normalize_model_family(result.get("model") or "")

        make_ok = got_make == true_make or got_make in true_make or true_make in got_make
        family_ok = got_family == true_family
        year_ok = true_year is not None and year_matches(result.get("year_estimate"), true_year)

        make_correct += make_ok
        model_family_correct += family_ok
        year_correct += year_ok

        if args.verbose:
            print(f"[{i}/{len(sample)}] truth: {true_year} {true_make} {true_family} | "
                  f"got: {result.get('year_estimate')} {got_make} {got_family} | "
                  f"make={'OK' if make_ok else 'X'} family={'OK' if family_ok else 'X'} year={'OK' if year_ok else 'X'}")

    elapsed = time.monotonic() - start
    n = len(sample)
    print("\n" + "=" * 60)
    print(f"model: {args.model} · samples: {n} · elapsed: {elapsed:.1f}s ({elapsed / n:.2f}s/photo)")
    print(f"make match:         {make_correct}/{n} ({make_correct / n:.0%})")
    print(f"model_family match: {model_family_correct}/{n} ({model_family_correct / n:.0%})")
    print(f"year within +/-1yr: {year_correct}/{n} ({year_correct / n:.0%})")
    if errors:
        print(f"extraction errors (fell back to unknown): {errors}/{n}")


if __name__ == "__main__":
    main()
