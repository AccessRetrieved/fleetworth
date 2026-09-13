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
    # Added to thicken model families that only showed up incidentally (a
    # handful of hits each) from the generic category keywords above —
    # each of these is a real, common truck that deserves its own comps
    # bucket instead of sitting at 1-10 comps.
    "Isuzu NPR",
    "International DuraStar",
    "Kenworth T880",
    "Western Star",
    "Mack Granite",
    # Year-backfill pass: DINO's visual similarity can't distinguish a
    # truck's age (a 2012 and 2020 Cascadia look nearly identical), but
    # price differs hugely by year — so pricing needs same-model comps
    # spread across years, not just clustered around recent inventory.
    # TruckPaper has no year-range URL param (checked); putting the year
    # first in the free-text keyword strongly biases results toward it.
    "2010 Freightliner Cascadia",
    "2012 Freightliner Cascadia",
    "2016 Freightliner Cascadia",
    "2013 Kenworth T680",
    "2016 Kenworth T680",
    "2018 Kenworth T680",
    "2008 Volvo VNL",
    "2014 Volvo VNL",
    "2017 Volvo VNL",
    "2009 Freightliner M2",
]

# Safety net: reject any listing whose category text looks like a pickup,
# in case a keyword match pulls one in (e.g. a Ford F-550 "Dump Truck" hit).
PICKUP_CATEGORY_MARKERS = ("pickup", "ton pickup")

BASE_URL = "https://www.truckpaper.com/listings/for-sale/trucks-and-trailers/all"
LISTINGS_PER_PAGE = 28
# Upper bound only — pagination normally ends earlier, at the last page
# implied by the "1 - 28 of N Listings" header or when results go stale.
# (A single keyword like "Freightliner Cascadia" has ~8k listings / ~290 pages.)
PAGES_PER_KEYWORD = 400
# Stop a keyword after this many consecutive pages where every card was
# already seen — deep pages of overlapping keywords are pure wasted loads.
STALE_PAGES_BEFORE_STOP = 3
DELAY_SECONDS = 2.5  # polite rate limit: minimum interval between page-load starts
# Only the server-rendered HTML is needed; skip heavy assets (also lighter on
# TruckPaper's servers). Scripts are left alone — Cloudflare's check needs them.
BLOCKED_RESOURCE_TYPES = ("image", "media", "font")
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "truckpaper_raw.jsonl"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

TITLE_RE = re.compile(r"^(\d{4})\s+(.+)$")
TOTAL_LISTINGS_RE = re.compile(r"of\s+([\d,]+)\s+Listings")


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


def extract_jsonld_images(soup) -> dict[str, str]:
    """listing_id -> image URL from the page's schema.org Product JSON-LD.

    Card <img> tags are rendered client-side only for the first few cards
    (~4 of 28), but the server HTML's JSON-LD block carries an image for
    every listing on the page."""
    images: dict[str, str] = {}
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except json.JSONDecodeError:
            continue
        for block in data if isinstance(data, list) else [data]:
            offers = block.get("offers") if isinstance(block, dict) else None
            for offer in (offers or {}).get("offers", []) if isinstance(offers, dict) else []:
                item = offer.get("itemOffered") or {}
                if item.get("productID") and item.get("image"):
                    images[str(item["productID"])] = item["image"]
    return images


def parse_card(wrapper, fallback_images: dict[str, str] | None = None) -> dict | None:
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

    # Only the first few cards have a rendered <img>; the rest fall back to
    # the JSON-LD image. Without the fallback most listings were silently
    # dropped as "no image".
    img_el = wrapper.select_one(".listing-main-img, .listing-main-image")
    image_url = (img_el.get("src") or img_el.get("data-uc-src")) if img_el else None
    if not image_url and fallback_images:
        image_url = fallback_images.get(listing_id)

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


def scrape(keywords: list[str] | None = None):
    """keywords: override SEARCH_KEYWORDS (e.g. to run a subset in parallel
    against the same output file — safe because seen_ids dedup means two
    concurrent runs on disjoint keyword sets won't double-write a listing
    unless the same one happens to surface under both, which clean_data.py's
    own dedupe pass catches anyway)."""
    keywords = keywords if keywords is not None else SEARCH_KEYWORDS
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

    # Ids filtered out (auction, no price, pickup) — persisted alongside the
    # output so the stale-page check doesn't mistake them for fresh results
    # on a rerun.
    rejected_path = OUTPUT_PATH.with_name(OUTPUT_PATH.stem + "_rejected_ids.txt")
    rejected_ids: set[str] = set()
    if rejected_path.exists():
        rejected_ids = set(rejected_path.read_text().split())

    # Per-keyword pagination progress, so a rerun (e.g. after raising
    # PAGES_PER_KEYWORD) continues deeper instead of re-walking early pages
    # and tripping the stale-page stop on them.
    progress_path = OUTPUT_PATH.with_name(OUTPUT_PATH.stem + "_progress.json")
    progress: dict[str, dict] = json.loads(progress_path.read_text()) if progress_path.exists() else {}

    total_new = 0
    last_load_start = 0.0
    with sync_playwright() as p, OUTPUT_PATH.open("a") as out_f, rejected_path.open("a") as rejected_f:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=USER_AGENT, viewport={"width": 1400, "height": 1000})
        page = context.new_page()
        page.route(
            "**/*",
            lambda route: route.abort()
            if route.request.resource_type in BLOCKED_RESOURCE_TYPES
            else route.continue_(),
        )

        for keyword in keywords:
            state = progress.get(keyword, {"pages_done": 0, "done": False})
            if state["done"]:
                print(f"  [{keyword}] already finished in a previous run — skipping")
                continue
            first_page = state["pages_done"] + 1
            total_pages = None  # from the result-count header; None if not found
            last_page = PAGES_PER_KEYWORD
            stale_pages = 0
            for page_num in range(first_page, PAGES_PER_KEYWORD + 1):
                if page_num > last_page:
                    break
                url = f"{BASE_URL}?Keywords={keyword.replace(' ', '+')}&Page={page_num}"

                # Rate limit counts load time toward the delay instead of
                # stacking a full sleep on top of every load.
                wait = DELAY_SECONDS - (time.monotonic() - last_load_start)
                if wait > 0:
                    time.sleep(wait)
                last_load_start = time.monotonic()
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_selector(".list-listing-card-wrapper", timeout=10000)
                except Exception as e:
                    print(f"  [{keyword} p{page_num}] no listings loaded ({type(e).__name__}) — stopping pagination for this keyword")
                    break

                soup = BeautifulSoup(page.content(), "lxml")
                wrappers = soup.select(".list-listing-card-wrapper")
                if not wrappers:
                    print(f"  [{keyword} p{page_num}] no listings — stopping pagination for this keyword")
                    break

                if page_num == first_page:
                    m = TOTAL_LISTINGS_RE.search(soup.get_text(" "))
                    if m:
                        total_listings = int(m.group(1).replace(",", ""))
                        total_pages = -(-total_listings // LISTINGS_PER_PAGE)
                        last_page = min(PAGES_PER_KEYWORD, total_pages)
                        print(f"  [{keyword}] {total_listings} listings → {last_page} pages")

                jsonld_images = extract_jsonld_images(soup)
                new_this_page = 0
                unseen_this_page = 0
                for wrapper in wrappers:
                    record = parse_card(wrapper, jsonld_images)
                    if not record or record["listing_id"] in seen_ids or record["listing_id"] in rejected_ids:
                        continue
                    unseen_this_page += 1
                    cat_lower = (record["category"] or "").lower()
                    if (
                        record["price"] is None or not record["image_urls"]  # missing price or images (Phase 1c)
                        or record["is_auction"]  # opening bid != market price, skews base-price averages
                        or any(marker in cat_lower for marker in PICKUP_CATEGORY_MARKERS)  # commercial trucks only
                    ):
                        rejected_ids.add(record["listing_id"])
                        rejected_f.write(record["listing_id"] + "\n")
                        continue
                    seen_ids.add(record["listing_id"])
                    out_f.write(json.dumps(record) + "\n")
                    out_f.flush()
                    new_this_page += 1
                    total_new += 1

                print(f"  [{keyword} p{page_num}/{last_page}] {len(wrappers)} cards, {new_this_page} new saved (total new: {total_new})")

                stale_pages = stale_pages + 1 if unseen_this_page == 0 else 0
                progress[keyword] = {
                    "pages_done": page_num,
                    # Hitting the PAGES_PER_KEYWORD cap isn't "done" — raising
                    # the cap later should continue from here.
                    "done": (total_pages is not None and page_num >= total_pages)
                    or stale_pages >= STALE_PAGES_BEFORE_STOP,
                }
                progress_path.write_text(json.dumps(progress, indent=2))
                if stale_pages >= STALE_PAGES_BEFORE_STOP:
                    print(f"  [{keyword}] {stale_pages} pages in a row with nothing unseen — moving to next keyword")
                    break

        browser.close()

    print(f"Done. {total_new} new listings saved to {OUTPUT_PATH}")


if __name__ == "__main__":
    import sys

    # Optional: pass keywords as args to run a subset (e.g. in a parallel
    # session alongside another run against the same output file), one
    # keyword per arg — quote multi-word ones: `uv run python
    # scrape_truckpaper.py "2010 Freightliner Cascadia" "2012 Freightliner Cascadia"`
    scrape(sys.argv[1:] or None)
