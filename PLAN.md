# Fleetworth — Build Plan

Webapp that predicts a used truck's price from a photo or video alone — no mileage, no VIN, no manual input. Built for 54 Hackathon FA26 (Supply Chain & Automation track, sponsored by Kamion).

## Architecture

```
Guided Live Capture — camera opens on page load, one continuous session:
  a guide walks the user around the truck while photos auto-snap along
  the way (flexible angles/count, ~3-7 typical); the whole session is
  also recorded as video, kept only as a real-life/anti-forgery record —
  the video is NOT sampled or fed into the pipeline below, only the
  snapped photos are
      │
      ▼
Vision API Extraction (per photo → structured JSON:
  make, model, year_estimate, trim, condition, visible_damage,
  tire_condition, confidence)
      │
      ▼
Multi-View Fusion (majority vote for make/model/year,
  worst-case for damage, average for wear scores;
  works across any 3-7 views, not fixed named waypoints)
      │
      ▼
Feature Encoding (condition/tire → numeric scores)
      │
      ▼
Pricing Logic (base price lookup from TruckPaper comps
  × condition/tire/damage adjustment)
      │
      ▼
Price Range + Confidence Score + Explainable Breakdown
  (no single point price as the headline output)
```

Design principles:
- The AI vision API extracts *what it sees* (structured facts), never the price itself. Price comes from a formula/model grounded in real scraped comps data. This is the core explainability story for the demo — the challenge brief explicitly says a thin "send photo to vision API, print the number" wrapper will lose ("We'll be able to tell"), so this separation is not optional polish, it's the core requirement.
- No vision model gets trained from scratch or fine-tuned — the VLM is used pretrained/as-is for extraction. The only "training" in this project is fitting a small tabular regression (Option B, seconds not hours) on the scraped comps dataset.
- A truck is too long to fit in one frame at useful detail, so capture is a single guided live session that auto-snaps several photos (~3-7 typical) as the user moves around the truck, instead of relying on one shot — this also keeps vision API calls to a handful per truck instead of processing a full video stream.
- **The recorded video and the snapped photos serve different, separate purposes.** Photos are the only thing that feeds the vision/pricing pipeline. The video exists purely as evidence the capture was a real live session (not a forged upload or stock photos) — it's stored as-is and not processed now. A later stretch could add automated liveness/forgery detection against the video, but that's explicitly out of scope for this build.
- **Don't over-constrain the capture guidance to fixed named angles** (e.g. exactly one "front" shot, one "driver side" shot). We don't control, and don't know in advance, what angles a judge's own unseen photos will use when they test the system directly — so the guidance during capture should encourage broad coverage (move around the truck, get the tires, etc.) without hard-requiring a rigid per-angle checklist, and the pipeline (Phase 3 fusion, Phase 4e limits) must already tolerate a variable, unlabeled set of views rather than assuming named waypoints.
- **The headline output is a price range + a confidence score, not a single point price.** A lone number reads as false precision for something priced off a handful of photos; the range and confidence are what the "why this price" explainability story is actually selling. An internal point estimate may still exist as an implementation detail (e.g. to compute the range), but it is never the primary thing shown or returned.
- **Interior/cab damage is out of scope.** Don't require or rely on an interior photo — it may not be provided at all, and internal condition/damage isn't part of this build's pricing signal. Fusion and the "missing view" check should only consider exterior views.
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

## Phase 2 — Guided Live Capture + Vision Extraction Pipeline

There is no image-vs-video choice for the user — capture is always one continuous live-camera session that produces two artifacts at once: a set of auto-snapped photos (which feed the vision pipeline) and a full video recording of that same session (kept only as a real-life/anti-forgery record, never processed by the pipeline). See design principles above for why these two are kept separate.

### 2a. Guided live capture (frontend)
- [ ] On page load, immediately open the device's camera feed — no upload picker, no mode selection, capture starts from a live view
- [ ] **Start** button begins recording the session as one video file and begins the capture guide
- [ ] While recording, a guide prompts the user to move around the truck (e.g. "walk to the front", "now the side", "get close to the tires") to encourage broad coverage — but this is guidance, not a rigid checklist: don't hard-require exactly one shot per named angle, since we don't know what angles a judge's own unseen test photos will use, and the pipeline (Phase 3, Phase 4e) is designed to work over however many views actually come in
- [ ] Auto-snap a photo periodically or when framing looks good during the session (reuse the in-browser detector idea — confirm a truck is in frame, not too close/cut off — as the trigger), aiming for roughly 3-7 photos per session without hard-failing if the count comes out higher or lower
- [ ] **Stop** button ends the recording
- [ ] **Submit** button uploads both artifacts to the backend: the auto-snapped photos (for Phase 2b extraction) and the full session video (stored as-is, not processed for pricing)
- [ ] **Interior/cab shots are out of scope** — the guide doesn't prompt for one, and a submission isn't penalized for not including it (internal damage isn't part of this build's pricing signal; see design principles above)

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
- [ ] Run one extraction call per auto-snapped photo only (~3-7 calls per truck) — the stored video is never sampled into frames or fed into extraction

---

## Phase 3 — Multi-View Fusion

Works across any number of usable views (3-7 typical) — not tied to a fixed count or fixed named waypoints, since exactly which angles the guided session actually snapped will vary.

- [ ] Make/model/year: majority vote across all extractions, or highest-confidence single view if votes are split
- [ ] Condition: take the *worst* (lowest) condition score seen across views — a single damaged panel shouldn't get diluted by clean views of other panels
- [ ] Damage list: union of all damage flags seen across views (dedupe similar entries) — exterior damage only, per the interior-out-of-scope decision above
- [ ] Tire condition: worst score seen across whichever views show the tires
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
- [ ] **Output a price range (e.g. ±15%) plus a confidence score — this is the headline result, not a single point price.** A point estimate may be computed internally to derive the range, but it is not the primary field surfaced to the user. Widen the range (and/or lower the confidence score) when extraction confidence is low

### 4c. Option B — Learned regression (STRETCH, only if Phase 1d comps-with-features data exists)
- [ ] Fit linear regression or small XGBoost on scraped comps: `price ~ condition_score + tire_score + damage_count + make/model/year bucket`
- [ ] Compare predictions against Option A on the same test cases
- [ ] Swap in as the default pricing function if it performs better/more sensibly — same function signature (`features_json → price`), so this is a drop-in replacement

### 4d. Fallback handling
- [ ] Unknown/unrecognized make-model-year combo → fall back to a generic "truck" average price, flag low confidence
- [ ] Very low extraction confidence → widen the price range, surface a warning in the UI

### 4e. Knowing its limits (required per challenge brief, not just error handling)
- [ ] **Not-a-truck detection**: if the VLM extraction indicates the subject isn't a truck (or confidence is near-zero on make/model), refuse to output a price — return a clear "this doesn't look like a truck" response instead of a number
- [ ] **Missing-critical-view detection**: photos aren't labeled by angle (capture is unstructured/guided, not a fixed named-waypoint checklist — see Phase 2a), so this has to be judged from content, not from a slot being empty: e.g. if none of the submitted photos give a usable read on tire condition, don't silently guess — flag exactly what's missing, matching the brief's own example ("send me a shot of the tires")
- [ ] **Blurry/unusable image detection**: reuse the blur-detection heuristic (Laplacian variance) per submitted photo; below threshold → drop that photo from the usable set rather than feeding a bad extraction into fusion
- [ ] **Confidence-gated response tiers**: define at least two response modes — "priced" (normal output) vs. "needs more info" (names the specific gap) — the UI (Phase 6) needs to render both, not just the happy path
- [ ] This phase directly maps to judging criterion "Does it know its limits?" — treat it as core scope, not a stretch goal, and make sure at least one demo test case (Phase 7) deliberately triggers it

---

## Phase 5 — Backend API

- [ ] `POST /predict` — accepts a submission from one guided capture session: the auto-snapped photos (3-7 typical) plus the one session video. Runs the pipeline on the photos only; stores the video as-is (e.g. to disk/blob storage) as an authenticity record — not processed now, but kept for a possible future forgery/liveness check. Returns a **price range + confidence score as the headline** (not a single point price):
  ```json
  {
    "status": "priced",
    "price_range": [16172, 21880],
    "confidence": 0.72,
    "breakdown": {
      "base_price": 28000,
      "make": "Ford", "model": "F-150", "year_estimate": "2018-2020",
      "condition": "fair",
      "damage": ["rust on rear fender", "cracked side mirror"],
      "tire_condition": "worn",
      "views_used": 5
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

- [ ] **No upload picker, no mode choice** — the page opens straight into a live camera feed with Start / Stop / Submit controls (see Phase 2a); this replaces any drag-and-drop upload flow
- [ ] Guide overlay during recording, prompting the user to move around the truck (loose guidance, not a rigid per-angle checklist — see Phase 2a)
- [ ] Auto-snap indicator so the user can see photos being captured during the session
- [ ] Submit sends both the snapped photos and the session video to the backend (Phase 5) in one request
- [ ] Loading state after submit (show progress if possible)
- [ ] Results view: headline is the **price range + confidence score** (not a single number), then the explainable breakdown (base price → condition → final), matching the "why this price" demo story
- [ ] **"Needs more info" view**: distinct from an error state — this is a successful, intended outcome per the brief, so design it to look deliberate (not a crash/broken page), e.g. "we need a clearer shot of X" with a way to add the missing photo and retry
- [ ] Basic error states (bad upload, API failure) — kept separate from the "needs more info" case above, since one is a system limitation being handled gracefully and the other is a genuine error

---

## Phase 6b — Stretch: Panorama Stitching (only if core pipeline is solid with time to spare)

- [ ] For a continuous single-side view (helps catch damage that spans a panel), stitch a pan-across-the-side video into one mosaic using `cv2.Stitcher_create()` (ORB/SIFT feature matching + homography)
- [ ] Feed the stitched mosaic as one extra image to the vision API for that side, in addition to (not instead of) the auto-snapped photos
- [ ] Known risk: reflective paint and repetitive panel textures can break feature matching — treat as a bonus demo moment, not a dependency

---

## Phase 7 — Demo Prep

- [ ] Select 3-4 test photo sets (captured via the real guided-capture flow, not curated stock photos) spanning: clean truck, visibly damaged truck, two different makes/models
- [ ] Prepare one deliberate hard case (ambiguous angle, unusual truck) to show graceful degradation
- [ ] **Prepare one deliberate "knows its limits" case** — a non-truck subject, or a session with the tires (or another useful view) deliberately skipped during capture — to actively demonstrate the Phase 4e refusal behavior live, since judges score this explicitly and will likely test it themselves with their own live capture session
- [ ] One-slide pipeline diagram (the architecture diagram at the top of this doc)
- [ ] Rehearse answer to "why not just ask the AI model for a price directly" — this is the strongest technical talking point (grounded comps data + explainable formula vs. ungrounded LLM guess), and it's a direct answer to the brief's own "what won't win" line
- [ ] Remember: judges bring their own unseen photos for a live appraisal — build and test for genuinely unfamiliar inputs, not just your curated demo set

---

## Notes / Open Decisions

- Vision API choice: TBD — pick based on available API credits
- Scraper scope: if time-constrained, limit to 3-5 common truck models (F-150, Silverado, Ram 1500, etc.) rather than all trucks, for better comps density per bucket
- Legal/ethical note: scrape respectfully (rate limits, robots.txt, no auth bypass) — this is a hackathon demo, not a production scraping operation
- In-browser detection library (Phase 2a): if TensorFlow.js/YOLO setup for the auto-snap trigger eats too much time, a simpler fallback (snap on a fixed timer instead of framing-based detection) is an acceptable substitute — don't let this polish block the core pipeline
- Interior/cab photos are explicitly out of scope for this build (not required, not penalized if missing, not fused/priced on) — we don't know if interior images will even be provided, and internal damage isn't part of the pricing signal here
- The session video is stored (disk/blob storage — mechanism TBD) purely as an authenticity record; no forgery/liveness detection is built against it in this pass, that's explicitly deferred to later
- Capture guidance is intentionally loose about exact angles (see Phase 2a) — don't design the pipeline or the demo around an assumption that photos arrive in a fixed order or fixed named set
