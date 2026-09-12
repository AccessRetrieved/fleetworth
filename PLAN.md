# Fleetworth — Build Plan

Webapp that predicts a used truck's price from a photo or video alone — no mileage, no VIN, no manual input. Built for 54 Hackathon FA26 (Supply Chain & Automation track, sponsored by Kamion).

## Architecture

```
Guided Waypoint Capture (front / driver side / rear / passenger side / tires)
      │
      ▼
Vision API Extraction (per waypoint → structured JSON:
  make, model, year_estimate, trim, condition, visible_damage,
  tire_condition, confidence)
      │
      ▼
Multi-View Fusion (majority vote for make/model/year,
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

Design principles:
- The AI vision API extracts *what it sees* (structured facts), never the price itself. Price comes from a formula/model grounded in real scraped comps data. This is the core explainability story for the demo — the challenge brief explicitly says a thin "send photo to vision API, print the number" wrapper will lose ("We'll be able to tell"), so this separation is not optional polish, it's the core requirement.
- No vision model gets trained from scratch or fine-tuned — the VLM is used pretrained/as-is for extraction. The only "training" in this project is fitting a small tabular regression (Option B, seconds not hours) on the scraped comps dataset.
- A truck is too long to fit in one frame at useful detail, so capture is structured around explicit waypoints instead of arbitrary video sampling — this also keeps vision API calls to ~5-8 per truck instead of processing a full video stream.
- The comps dataset and the query truck's photos are decoupled: comps only need one decent photo per listing for condition scoring, and don't need to match the query's capture angles (see Phase 1b note).
- **The system must know its limits.** Per the challenge brief, confidently pricing a blurry photo, a non-truck, or a truck with missing critical views is a losing outcome — refusing or asking for a specific missing shot ("send me a shot of the tires") is explicitly called out as a feature, not a cop-out. See Phase 4e.

---

## Phase 0 — Setup

- [ ] Initialize repo structure: `/scraper`, `/backend`, `/frontend`, `/data`
- [ ] Set up Python environment for scraper + backend (venv/poetry)
- [ ] Get vision API key working (Claude or GPT-4V or Gemini — pick one, confirm credits/access)
- [ ] Set up basic web framework for backend (FastAPI recommended — fast to stand up, easy JSON endpoints)
- [ ] Frontend: Python Flask, owned by another team member on a separate branch, merged into `main` when ready — not part of this workstream

---

## Phase 1 — TruckPaper.com Scraper (comps dataset)

Goal: build a dataset of `{image_url(s), make, model, year, trim, price, mileage, condition_notes}` — this becomes the base-price lookup table AND the training data for the pricing regression. This dataset is required regardless of pricing option chosen — it's what grounds the price in real sales data instead of an AI guess (see Pricing Logic notes below).

### 1a. Reconnaissance
- [ ] Manually browse truckpaper.com search/listing pages, identify:
  - Search/listing URL structure (pagination, filters by make/model/year)
  - Whether listing data is server-rendered HTML or loaded via JS/XHR (check Network tab — if there's a JSON API backing the search results, scrape that directly instead of parsing HTML)
  - robots.txt rules — check `truckpaper.com/robots.txt` and respect disallowed paths/rate limits
- [ ] Identify the HTML structure (or API response schema) for: listing price, year, make, model, trim, mileage, location, image URLs, listing detail page URL

### 1b. Scraper implementation
- [ ] Build a listing-index scraper: paginate through search results (filter to trucks generally, or specific makes if time-constrained — e.g. Ford F-150, Chevy Silverado, Ram 1500 as a starting set for good comps density)
- [ ] For each listing, extract: `listing_id, make, model, year, trim, price, mileage, location, image_urls[], detail_url`
- [ ] Download images locally (or just store URLs and fetch on demand) — **one decent primary photo per listing is sufficient**; angle doesn't need to match the query truck's capture waypoints, since comps and query are only ever compared at the level of extracted numeric features (condition_score, etc.), never pixel-to-pixel
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

## Phase 2 — Guided Waypoint Capture + Vision Extraction Pipeline

A single frame can't fit a whole truck at useful detail, so capture is structured around explicit waypoints instead of processing an arbitrary video stream.

### 2a. Guided capture (frontend + lightweight in-browser detection)
- [ ] Define capture waypoints: front, driver side (front-half + rear-half if long), rear, passenger side (same), tires/wheels close-up, **cab interior/dash (required, not optional)** (~6-9 shots total) — the challenge brief explicitly names cab interior as a condition signal buyers want, so don't let this one be skippable the way "if accessible" implied before
- [ ] UI walks the user through each waypoint in sequence ("show me the front", "now the driver side", ...)
- [ ] Run a lightweight in-browser object detector (TensorFlow.js + small YOLO model, or a simple bounding-box tracker) on the live camera feed to:
  - Confirm a truck is actually in frame before allowing capture
  - Warn if too close (edges cut off) — prompt "step back"
  - Auto-capture when framing looks good, or let user tap to confirm
- [ ] Only send the selected keyframe per waypoint to the backend (not the full stream) — keeps API calls to ~5-8 per truck
- [ ] Fallback: if the browser-based flow is too much for the timeline, accept a short pre-recorded video and just sample the frame(s) with the largest/most-centered truck bounding box per rough time-segment as a stand-in for waypoints

### 2b. Vision extraction
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
- [ ] Run one extraction call per captured waypoint image (~5-8 calls per truck, not per video frame)

---

## Phase 3 — Multi-View Fusion

- [ ] Make/model/year: majority vote across waypoint extractions, or highest-confidence single view if votes are split
- [ ] Condition: take the *worst* (lowest) condition score seen across views — a single damaged panel shouldn't get diluted by clean views of other panels
- [ ] Damage list: union of all damage flags seen across views (dedupe similar entries)
- [ ] Tire condition: worst score seen (from the dedicated tire waypoint)
- [ ] Output one fused JSON per upload, same schema as single-view extraction

---

## Phase 4 — Pricing Logic

Note: nothing in this phase trains a vision model. Option A is pure arithmetic against the scraped dataset. Option B trains a small tabular regression (seconds, `sklearn`/`xgboost` `.fit()`) — not a deep learning run — on the same scraped dataset. Either way, the scraped dataset is what grounds the output price in real sales data instead of an AI-guessed number; skipping it removes the entire reason this approach avoids hallucinated prices.

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

### 4e. Knowing its limits (required per challenge brief, not just error handling)
- [ ] **Not-a-truck detection**: if the VLM extraction indicates the subject isn't a truck (or confidence is near-zero on make/model), refuse to output a price — return a clear "this doesn't look like a truck" response instead of a number
- [ ] **Missing-critical-view detection**: if a required waypoint (e.g. tires, or any side) is missing, blank, or too blurry/dark to assess, don't silently guess — flag exactly which view is missing and what's needed, matching the brief's own example ("send me a shot of the tires")
- [ ] **Blurry/unusable image detection**: reuse the blur-detection heuristic (Laplacian variance) per waypoint image; below threshold → treat that waypoint as missing rather than feeding a bad extraction into fusion
- [ ] **Confidence-gated response tiers**: define at least two response modes — "priced" (normal output) vs. "needs more info" (names the specific gap) — the UI (Phase 6) needs to render both, not just the happy path
- [ ] This phase directly maps to judging criterion "Does it know its limits?" — treat it as core scope, not a stretch goal, and make sure at least one demo test case (Phase 7) deliberately triggers it

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
- [ ] Error handling: invalid file type, API timeout/rate limit
- [ ] Wire the "needs more info" response tier from Phase 4e as a first-class API response shape (not an HTTP error) — e.g.:
  ```json
  {
    "status": "needs_more_info",
    "reason": "no clear view of tires detected",
    "message": "Can't assess tire condition — please add a close-up photo of the tires."
  }
  ```

---

## Phase 6 — Frontend

**Owned by another team member, built in Python Flask on a separate branch, merged into `main` once ready — not part of the backend/scraper workstream.**

- [ ] Upload UI (drag-and-drop image or video)
- [ ] Loading state (video processing will take longer — show progress if possible)
- [ ] Results view: headline price + range, then the explainable breakdown (base price → condition → final), matching the "why this price" demo story
- [ ] **"Needs more info" view**: distinct from an error state — this is a successful, intended outcome per the brief, so design it to look deliberate (not a crash/broken page), e.g. "we need a clearer shot of X" with a way to add the missing photo and retry
- [ ] Basic error states (bad upload, API failure) — kept separate from the "needs more info" case above, since one is a system limitation being handled gracefully and the other is a genuine error

---

## Phase 6b — Stretch: Panorama Stitching (only if core pipeline is solid with time to spare)

- [ ] For a continuous single-side view (helps catch damage that spans a panel), stitch a pan-across-the-side video into one mosaic using `cv2.Stitcher_create()` (ORB/SIFT feature matching + homography)
- [ ] Feed the stitched mosaic as one extra image to the vision API for that side, in addition to (not instead of) the waypoint shots
- [ ] Known risk: reflective paint and repetitive panel textures can break feature matching — treat as a bonus demo moment, not a dependency

---

## Phase 7 — Demo Prep

- [ ] Select 3-4 test videos/images spanning: clean truck, visibly damaged truck, two different makes/models
- [ ] Prepare one deliberate hard case (ambiguous angle, unusual truck) to show graceful degradation
- [ ] **Prepare one deliberate "knows its limits" case** — a non-truck photo, or a set with the tire/interior shot deliberately missing — to actively demonstrate the Phase 4e refusal behavior live, since judges score this explicitly and will likely test it themselves with unseen photos
- [ ] One-slide pipeline diagram (the architecture diagram at the top of this doc)
- [ ] Rehearse answer to "why not just ask the AI model for a price directly" — this is the strongest technical talking point (grounded comps data + explainable formula vs. ungrounded LLM guess), and it's a direct answer to the brief's own "what won't win" line
- [ ] Remember: judges bring their own unseen photos for a live appraisal — build and test for genuinely unfamiliar inputs, not just your curated demo set

---

## Notes / Open Decisions

- Vision API choice: TBD — pick based on available API credits
- Scraper scope: if time-constrained, limit to 3-5 common truck models (F-150, Silverado, Ram 1500, etc.) rather than all trucks, for better comps density per bucket
- Legal/ethical note: scrape respectfully (rate limits, robots.txt, no auth bypass) — this is a hackathon demo, not a production scraping operation
- In-browser detection library (Phase 2a): if TensorFlow.js/YOLO setup eats too much time, the simpler fallback (accept pre-recorded video, pick best frame per time-segment) is an acceptable substitute — don't let waypoint UI polish block the core pipeline
