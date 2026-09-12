"""
Scrapes TruckPaper.com search-result listings for a set of truck models and
saves raw records to /data/truckpaper_raw.jsonl.

TruckPaper sits behind a Cloudflare bot challenge, so plain HTTP requests
(requests/curl) get a 403. A real Playwright-driven Chromium browser clears
the challenge automatically, so we use that instead.

Listing pages are server-rendered (confirmed via recon) — no need to hit a
hidden JSON API. Pagination is a simple `?Page=N` query param, 28 listings
per page.

Usage:
    uv run python scrape_truckpaper.py
"""
import json
import re
import time
from pathlib import Path

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

# Commercial trucks only — no pickups. Mix of semi tractors (by make/model,
# since "semi truck" isn't a useful keyword on its own) and medium-duty work
# truck categories, per user direction.
SEARCH_KEYWORDS = [
    # Semi trucks / tractors
    "Freightliner Cascadia",
    "Peterbilt 389",
    "Kenworth T680",
    "Volvo VNL",
    "International LT",
    # Medium-duty work trucks
    "Dump Truck",
    "Box Truck",
    "Service Truck",
    "Freightliner M2",
]

# Safety net: reject any listing whose category text looks like a pickup,
# in case a keyword match pulls one in (e.g. a Ford F-550 "Dump Truck" hit).
PICKUP_CATEGORY_MARKERS = ("pickup", "ton pickup")

BASE_URL = "https://www.truckpaper.com/listings/for-sale/trucks-and-trailers/all"
PAGES_PER_KEYWORD = 12  # ~28 listings/page; yield varies (some pages are auction-heavy)
DELAY_SECONDS = 2.5  # polite rate limit between page loads
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "truckpaper_raw.jsonl"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

TITLE_RE = re.compile(r"^(\d{4})\s+(.+)$")


def parse_price(text: str | None) -> float | None:
    if not text:
        return None
    digits = re.sub(r"[^\d.]", "", text)
    return float(digits) if digits else None


def parse_mileage(spec_value: str | None) -> int | None:
    if not spec_value:
        return None
    digits = re.sub(r"[^\d]", "", spec_value)
    return int(digits) if digits else None


def parse_card(wrapper) -> dict | None:
    inner = wrapper.find("div", recursive=False)
    listing_id = inner.get("id") if inner else None
    if not listing_id:
        return None

    title_link = wrapper.select_one(".list-listing-title-link")
    if not title_link:
        return None
    title_text = title_link.get_text(strip=True)
    detail_url = title_link.get("href")
    if detail_url and detail_url.startswith("/"):
        detail_url = "https://www.truckpaper.com" + detail_url

    m = TITLE_RE.match(title_text)
    year, make_model = (m.group(1), m.group(2)) if m else (None, title_text)
    parts = make_model.split(None, 1)
    make = parts[0] if parts else None
    model = parts[1] if len(parts) > 1 else None

    category_el = wrapper.select_one(".listing-category")
    category = category_el.get_text(strip=True) if category_el else None

    # Regular listings show ".price"; auction listings show an opening bid
    # via ".auction-price" instead — treat that as the price too, flagged.
    price_el = wrapper.select_one(".price")
    is_auction = False
    if not price_el:
        price_el = wrapper.select_one(".auction-price")
        is_auction = price_el is not None
    price = parse_price(price_el.get_text(strip=True) if price_el else None)

    loc_el = wrapper.select_one(".machine-location")
    location = None
    if loc_el:
        location = loc_el.get_text(strip=True).replace("Location:", "").strip()

    mileage = None
    for spec in wrapper.select(".list-spec .spec"):
        label = spec.select_one(".spec-label")
        value = spec.select_one(".spec-value")
        if label and value and "mileage" in label.get_text(strip=True).lower():
            mileage = parse_mileage(value.get_text(strip=True))
            break

    img_el = wrapper.select_one(".listing-main-img")
    image_url = img_el.get("src") if img_el else None

    return {
        "listing_id": listing_id,
        "title": title_text,
        "year": year,
        "make": make,
        "model": model,
        "category": category,
        "price": price,
        "is_auction": is_auction,
        "mileage": mileage,
        "location": location,
        "image_urls": [image_url] if image_url else [],
        "detail_url": detail_url,
    }


def scrape():
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    seen_ids: set[str] = set()

    # Resume support: skip ids already saved from a previous run.
    if OUTPUT_PATH.exists():
        with OUTPUT_PATH.open() as f:
            for line in f:
                try:
                    seen_ids.add(json.loads(line)["listing_id"])
                except (json.JSONDecodeError, KeyError):
                    continue
        print(f"Resuming — {len(seen_ids)} listings already saved.")

    total_new = 0
    with sync_playwright() as p, OUTPUT_PATH.open("a") as out_f:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=USER_AGENT, viewport={"width": 1400, "height": 1000})
        page = context.new_page()

        for keyword in SEARCH_KEYWORDS:
            for page_num in range(1, PAGES_PER_KEYWORD + 1):
                url = f"{BASE_URL}?Keywords={keyword.replace(' ', '+')}&Page={page_num}"
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                except Exception as e:
                    print(f"  [skip] {url} — navigation error: {e}")
                    continue
                time.sleep(DELAY_SECONDS)

                soup = BeautifulSoup(page.content(), "lxml")
                wrappers = soup.select(".list-listing-card-wrapper")
                if not wrappers:
                    print(f"  [{keyword} p{page_num}] no listings — stopping pagination for this keyword")
                    break

                new_this_page = 0
                for wrapper in wrappers:
                    record = parse_card(wrapper)
                    if not record or record["listing_id"] in seen_ids:
                        continue
                    if record["price"] is None or not record["image_urls"]:
                        continue  # drop rows with missing price or images (Phase 1c)
                    if record["is_auction"]:
                        continue  # opening bid != market price, skews base-price averages
                    cat_lower = (record["category"] or "").lower()
                    if any(marker in cat_lower for marker in PICKUP_CATEGORY_MARKERS):
                        continue  # commercial trucks only, no pickups
                    seen_ids.add(record["listing_id"])
                    out_f.write(json.dumps(record) + "\n")
                    out_f.flush()
                    new_this_page += 1
                    total_new += 1

                print(f"  [{keyword} p{page_num}] {len(wrappers)} cards, {new_this_page} new saved (total new: {total_new})")

        browser.close()

    print(f"Done. {total_new} new listings saved to {OUTPUT_PATH}")


if __name__ == "__main__":
    scrape()
