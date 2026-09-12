# Fleetworth — Build Plan

Webapp that predicts a used truck's price from a photo or video alone — no mileage, no VIN, no manual input. Built for 54 Hackathon FA26 (Supply Chain & Automation track, sponsored by Kamion).

## Architecture

```
Image/Video Upload
      │
      ▼
Frame Extraction (video → frames, drop blurry/duplicate)
      │
      ▼
Vision API Extraction (per frame → structured JSON:
  make, model, year_estimate, trim, condition, visible_damage,
  tire_condition, confidence)
      │
      ▼
Multi-Frame Fusion (majority vote for make/model/year,
  worst-case for damage, average for wear scores)
      │
      ▼
Feature Encoding (condition/tire → numeric scores)
      │
      ▼
Pricing Logic (base price lookup from TruckPaper comps
  × condition/tire/damage adjustment)
      │
      ▼
Price Output + Confidence Range + Explainable Breakdown
```

Design principle: the AI vision API extracts *what it sees* (structured facts), never the price itself. Price comes from a formula/model grounded in real scraped comps data. This is the core explainability story for the demo.

---

## Phase 0 — Setup

- [ ] Initialize repo structure: `/scraper`, `/backend`, `/frontend`, `/data`
- [ ] Set up Python environment for scraper + backend (venv/poetry)
- [ ] Get vision API key working (Claude or GPT-4V or Gemini — pick one, confirm credits/access)
- [ ] Set up basic web framework for backend (FastAPI recommended — fast to stand up, easy JSON endpoints)
- [ ] Set up basic frontend scaffold (plain React/Vite or simple HTML+JS — keep it minimal, this is not the hard part)

---

## Phase 1 — TruckPaper.com Scraper (comps dataset)

Goal: build a dataset of `{image_url(s), make, model, year, trim, price, mileage, condition_notes}` — this becomes the base-price lookup table AND the training data for the pricing model.

### 1a. Reconnaissance
- [ ] Manually browse truckpaper.com search/listing pages, identify:
  - Search/listing URL structure (pagination, filters by make/model/year)
  - Whether listing data is server-rendered HTML or loaded via JS/XHR (check Network tab — if there's a JSON API backing the search results, scrape that directly instead of parsing HTML)
  - robots.txt rules — check `truckpaper.com/robots.txt` and respect disallowed paths/rate limits
- [ ] Identify the HTML structure (or API response schema) for: listing price, year, make, model, trim, mileage, location, image URLs, listing detail page URL

### 1b. Scraper implementation
- [ ] Build a listing-index scraper: paginate through search results (filter to trucks generally, or specific makes if time-constrained — e.g. Ford F-150, Chevy Silverado, Ram 1500 as a starting set for good comps density)
- [ ] For each listing, extract: `listing_id, make, model, year, trim, price, mileage, location, image_urls[], detail_url`
- [ ] Download images locally (or just store URLs and fetch on demand) — keep 3-5 images per listing if available (multiple angles help condition-labeling later)
- [ ] Add polite scraping practices: rate limiting/delay between requests, realistic user-agent, retry/backoff on errors, respect robots.txt
- [ ] Save raw scraped data to `/data/truckpaper_raw.jsonl` (one JSON object per line, easy to append/resume)

### 1c. Cleaning + structuring
- [ ] Dedupe listings (relist detection by VIN/title if visible, or fuzzy match)
- [ ] Drop rows with missing price or missing images
- [ ] Normalize make/model naming (e.g. "F-150" vs "F150" vs "Ford F150")
- [ ] Bucket by `(make, model, year)` and compute average price per bucket → this is `p_base` lookup table, save as `/data/base_prices.json`
- [ ] Target: 100+ listings if feasible, 30-50 minimum viable for demo

### 1d. Stretch — run extraction pipeline on comps too
- [ ] Once Phase 2 (vision extraction) is working, run it on the scraped comp images as well
- [ ] This gives every comp both a real sale price AND an extracted condition JSON — needed for Option B (learned regression) pricing

---

## Phase 2 — Vision Extraction Pipeline

- [ ] Frame extraction from video: sample ~1 frame/sec, use Laplacian variance to drop blurry frames, dedupe near-identical frames
- [ ] Write the structured-extraction prompt for the vision API:
  ```
  Given this image of a truck, return ONLY valid JSON:
  {
    "make": string, "model": string, "year_estimate": string (range ok),
    "trim": string, "condition": "excellent"|"good"|"fair"|"poor",
    "visible_damage": [string], "tire_condition": "new"|"worn"|"bald",
    "confidence": float 0-1
  }
  ```
- [ ] Test on 10+ sample images (mix from scraped data), iterate prompt until reliably valid + reasonably accurate
- [ ] Wrap API call with JSON parsing + validation, handle malformed responses (retry once, then fallback to "unknown")
- [ ] Run extraction per selected frame; for video, run on 3-8 representative frames (not every frame — cost/latency)

---

## Phase 3 — Multi-Frame Fusion

- [ ] Make/model/year: majority vote across frames, or highest-confidence single frame if votes are split
- [ ] Condition: take the *worst* (lowest) condition score seen across frames — a single damaged panel shouldn't get diluted by clean frames of other panels
- [ ] Damage list: union of all damage flags seen across frames (dedupe similar entries)
- [ ] Tire condition: worst score seen
- [ ] Output one fused JSON per upload, same schema as single-frame extraction

---

## Phase 4 — Pricing Logic

### 4a. Feature encoding
- [ ] Map categorical condition/tire values to numeric scores (see below)
- [ ] Count damage flags

```python
condition_map = {"excellent": 1.0, "good": 0.8, "fair": 0.55, "poor": 0.3}
tire_map = {"new": 1.0, "worn": 0.6, "bald": 0.2}
```

### 4b. Option A — Hand-tuned formula (BUILD THIS FIRST)
- [ ] Look up `p_base` from `/data/base_prices.json` by (make, model, year)
- [ ] Apply formula:
  ```
  price = p_base * (0.5 + 0.3*condition_score + 0.15*tire_score) * (1 - 0.05*damage_count)
  ```
- [ ] Sanity-check output against 3-5 known real listings, adjust coefficients if wildly off
- [ ] Output a ± range (e.g. ±15%) alongside the point estimate, scaled down if confidence is low

### 4c. Option B — Learned regression (STRETCH, only if Phase 1d comps-with-features data exists)
- [ ] Fit linear regression or small XGBoost on scraped comps: `price ~ condition_score + tire_score + damage_count + make/model/year bucket`
- [ ] Compare predictions against Option A on the same test cases
- [ ] Swap in as the default pricing function if it performs better/more sensibly — same function signature (`features_json → price`), so this is a drop-in replacement

### 4d. Fallback handling
- [ ] Unknown/unrecognized make-model-year combo → fall back to a generic "truck" average price, flag low confidence
- [ ] Very low extraction confidence → widen the price range, surface a warning in the UI

---

## Phase 5 — Backend API

- [ ] `POST /predict` — accepts image or video file, runs full pipeline, returns:
  ```json
  {
    "price_estimate": 19026,
    "price_range": [16172, 21880],
    "breakdown": {
      "base_price": 28000,
      "make": "Ford", "model": "F-150", "year_estimate": "2018-2020",
      "condition": "fair",
      "damage": ["rust on rear fender", "cracked side mirror"],
      "tire_condition": "worn",
      "confidence": 0.72
    }
  }
  ```
- [ ] Error handling: invalid file type, API timeout/rate limit, no truck detected in image

---

## Phase 6 — Frontend

- [ ] Upload UI (drag-and-drop image or video)
- [ ] Loading state (video processing will take longer — show progress if possible)
- [ ] Results view: headline price + range, then the explainable breakdown (base price → condition → final), matching the "why this price" demo story
- [ ] Basic error states (bad upload, low-confidence result)

---

## Phase 7 — Demo Prep

- [ ] Select 3-4 test videos/images spanning: clean truck, visibly damaged truck, two different makes/models
- [ ] Prepare one deliberate hard case (ambiguous angle, unusual truck) to show graceful degradation
- [ ] One-slide pipeline diagram (the architecture diagram at the top of this doc)
- [ ] Rehearse answer to "why not just ask the AI model for a price directly" — this is the strongest technical talking point (grounded comps data + explainable formula vs. ungrounded LLM guess)

---

## Notes / Open Decisions

- Vision API choice: TBD — pick based on available API credits
- Scraper scope: if time-constrained, limit to 3-5 common truck models (F-150, Silverado, Ram 1500, etc.) rather than all trucks, for better comps density per bucket
- Legal/ethical note: scrape respectfully (rate limits, robots.txt, no auth bypass) — this is a hackathon demo, not a production scraping operation
