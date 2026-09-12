"""Sanity-check the vision extraction module against real scraped images.

Usage:
    uv run python test_extraction.py                  # 10-sample dataset test (default)
    uv run python test_extraction.py path/to/image.jpg # run extraction on just this one image
    uv run python test_extraction.py https://...       # also works with a URL
"""
import json
import random
import sys
from pathlib import Path

from vision_extract import extract_from_image, _client

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "truckpaper_clean.jsonl"


def upsize(url: str) -> str:
    """Scraped image_urls are small listing-grid thumbnails (250x220,
    cropped). Request a bigger, uncropped version from the same media
    service for extraction accuracy."""
    return url.replace("w=250&h=220", "w=1024&h=768").replace("sz=Cover", "sz=Max")


def run_single(image_path: str):
    """Run extraction on one user-specified image (local path or URL) and
    just print the raw result — no ground truth to compare against."""
    client = _client()
    result = extract_from_image(image_path, client=client)
    print("=" * 80)
    print(f"IMAGE: {image_path}")
    print("EXTRACTED:")
    print(json.dumps(result, indent=2))


def run_dataset_sample():
    rows = [json.loads(l) for l in DATA_PATH.open()]
    random.seed(42)
    sample = random.sample(rows, 10)

    client = _client()
    correct_make = 0
    for r in sample:
        image_url = upsize(r["image_urls"][0])
        result = extract_from_image(image_url, client=client)
        print("=" * 80)
        print(f"GROUND TRUTH: {r['year']} {r['make']} {r['model']}  (listed ${r['price']:,.0f})")
        print(f"IMAGE: {image_url}")
        print("EXTRACTED:")
        print(json.dumps(result, indent=2))
        if result["make"].strip().lower() in r["make"].strip().lower() or r["make"].strip().lower() in result["make"].strip().lower():
            correct_make += 1

    print("=" * 80)
    print(f"Make matched ground truth: {correct_make}/{len(sample)}")


def main():
    if len(sys.argv) > 1:
        run_single(sys.argv[1])
    else:
        run_dataset_sample()


if __name__ == "__main__":
    main()
