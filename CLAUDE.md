# Note to Claude Code — data-expansion branch

You're helping a teammate on the **Fleetworth** hackathon project (used-truck pricing from photos). Read `PLAN.md` at the repo root first for full context — this note is just the specific task for this branch.

## The task

Expand and improve `data/base_prices.json` — the base-price lookup table the pricing formula multiplies against. It's built entirely from real scraped TruckPaper.com listings (no synthetic data), via:

1. `scraper/scrape_truckpaper.py` — Playwright-driven scraper (TruckPaper sits behind Cloudflare, so plain `requests`/`curl` gets blocked — a real browser is required). Searches by keyword, paginates, extracts `{listing_id, make, model, year, price, mileage, location, image_urls, category, is_auction}` per listing, skips auction listings (opening bids aren't real sale prices) and anything missing price/image.
2. `scraper/clean_data.py` — normalizes make/model naming (handles split multi-word makes like "WESTERN STAR", collapses model variants like "CASCADIA 126"/"CASCADIA 125" into one family "CASCADIA"), dedupes, buckets by `(make, model_family, year)`, computes `{avg, min, max, count}` per bucket, writes `data/base_prices.json`.

Currently: **375 listings, 164 buckets, but 124 of those 164 (76%) have only 1-2 comps** — meaning most bucket "averages" are really just one listing's asking price standing in for the whole bucket. That's the main accuracy problem to fix.

## Where to focus

- **More keywords/makes in `SEARCH_KEYWORDS`** (currently just 9: Freightliner Cascadia, Peterbilt 389, Kenworth T680, Volvo VNL, International LT, Dump Truck, Box Truck, Service Truck, Freightliner M2). Adding Mack, Western Star, Isuzu, Hino, more Peterbilt/Kenworth model numbers, etc. broadens coverage and thickens existing sparse buckets.
- **More pages per keyword** (`PAGES_PER_KEYWORD`, currently 12) — straightforward way to get more comps per existing bucket.
- **Better model-family normalization** in `clean_data.py`'s `MODEL_FAMILY_PATTERNS` — the regex list is short; more variants correctly collapsing into one family means fewer artificially-fragmented sparse buckets. Check `truckpaper_clean.jsonl` for model strings that *should* be grouped but aren't yet.
- **Outlier handling** — buckets currently use a plain mean; consider median or trimming extreme values so one abnormally priced listing doesn't skew a whole bucket.
- **(Bigger lever, optional)** Mileage-aware bucketing — `base_prices.json` currently ignores mileage entirely, so a bucket blends low- and high-mileage trucks of the same year. Not required by the current plan, but flagged as a real accuracy improvement if there's time.

## Constraints to respect (from PLAN.md)

- Scrape respectfully: rate limiting, robots.txt, no auth bypass — this is a hackathon demo, not a production scraping operation. The scraper already does this (2.5s delay between page loads); keep it.
- Keep excluding auction listings (`is_auction`) — opening bids are not market prices and will drag bucket averages down badly if let back in.
- `data/truckpaper_raw.jsonl` supports resume (it skips already-seen `listing_id`s on rerun) — you can just rerun the scraper with expanded keywords/pages rather than starting over.

## How to run it

```bash
cd scraper
uv run python scrape_truckpaper.py   # appends to ../data/truckpaper_raw.jsonl
uv run python clean_data.py          # rebuilds ../data/truckpaper_clean.jsonl and ../data/base_prices.json
```

## When done

Open a PR from this branch into `main`. `backend/pricing.py`'s `base_price_lookup()` reads `data/base_prices.json` directly — no code changes needed on the backend side unless you change the bucket key shape (currently `base_prices[MAKE][MODEL_FAMILY][YEAR] = {avg, min, max, count}`); if you do change the shape, flag it, since `backend/pricing.py` and `backend/pricing_formula.py` (on the `backend` branch) depend on it exactly as-is.
