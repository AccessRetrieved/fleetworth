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
- **An interior/cabin photo is optional, offered (not required).** The exterior guided capture never requires or prompts for one mid-session — but right before Submit, the user is asked via a toggle whether they'd like to add one. If yes, that photo goes through the same extraction as any other photo and *does* feed into condition/pricing, tightening the estimate. If no, the estimate proceeds on exterior-only info with a **wider, more conservative range** (see Phase 4b) and the result explicitly says why the range is wide. Either way this is never a "missing view" refusal (Phase 4e) — an omitted interior photo is an accepted, priced outcome, just a less certain one.
- The comps dataset and the query truck's photos are decoupled: comps only need one decent photo per listing for condition scoring, and don't need to match the query's capture angles (see Phase 1b note).
- **The system must know its limits.** Per the challenge brief, confidently pricing a blurry photo, a non-truck, or a truck with missing critical views is a losing outcome — refusing or asking for a specific missing shot ("send me a shot of the tires") is explicitly called out as a feature, not a cop-out. See Phase 4e.

---

## Phase 0 — Setup

- [x] Initialize repo structure: `/scraper`, `/backend`, `/frontend`, `/data`
- [x] Set up Python environment for scraper + backend (venv/poetry)
- [x] Get vision API key working (Claude or GPT-4V or Gemini — pick one, confirm credits/access)
- [x] Set up basic web framework for backend (FastAPI recommended — fast to stand up, easy JSON endpoints)
- [ ] Frontend: Python Flask, owned by another team member on a separate branch, merged into `main` when ready — not part of this workstream

---

## Phase 1 — TruckPaper.com Scraper (comps dataset)

Goal: build a dataset of `{image_url(s), make, model, year, trim, price, mileage, condition_notes}` — this becomes the base-price lookup table AND the training data for the pricing regression. This dataset is required regardless of pricing option chosen — it's what grounds the price in real sales data instead of an AI guess (see Pricing Logic notes below).

### 1a. Reconnaissance
- [x] Manually browse truckpaper.com search/listing pages, identify:
  - Search/listing URL structure (pagination, filters by make/model/year)
  - Whether listing data is server-rendered HTML or loaded via JS/XHR (check Network tab — if there's a JSON API backing the search results, scrape that directly instead of parsing HTML)
  - robots.txt rules — check `truckpaper.com/robots.txt` and respect disallowed paths/rate limits
- [x] Identify the HTML structure (or API response schema) for: listing price, year, make, model, trim, mileage, location, image URLs, listing detail page URL

### 1b. Scraper implementation
- [x] Build a listing-index scraper: paginate through search results (filter to trucks generally, or specific makes if time-constrained — e.g. Ford F-150, Chevy Silverado, Ram 1500 as a starting set for good comps density)
- [x] For each listing, extract: `listing_id, make, model, year, trim, price, mileage, location, image_urls[], detail_url`
- [x] Download images locally (or just store URLs and fetch on demand) — **one decent primary photo per listing is sufficient**; angle doesn't need to match the query truck's capture waypoints, since comps and query are only ever compared at the level of extracted numeric features (condition_score, etc.), never pixel-to-pixel
- [x] Add polite scraping practices: rate limiting/delay between requests, realistic user-agent, retry/backoff on errors, respect robots.txt
- [x] Save raw scraped data to `/data/truckpaper_raw.jsonl` (one JSON object per line, easy to append/resume)

### 1c. Cleaning + structuring
- [x] Dedupe listings (relist detection by VIN/title if visible, or fuzzy match)
- [x] Drop rows with missing price or missing images
- [x] Normalize make/model naming (e.g. "F-150" vs "F150" vs "Ford F150")
- [x] Bucket by `(make, model, year)` and compute average price per bucket → this is `p_base` lookup table, save as `/data/base_prices.json`
- [x] Target: 100+ listings if feasible, 30-50 minimum viable for demo

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
- [ ] After Stop, before Submit: show a toggle/prompt asking "Add a photo of the interior/cabin?" — this is a separate, explicit step, not part of the live guided sequence (the guide itself never prompts for interior mid-session, and this photo is never part of the recorded video)
  - If the user opts in, reveal a single-image upload control (plain file/camera picker, not the live guided-capture UI) for exactly one interior/cabin photo
  - That uploaded photo is included in what's sent to the backend and does go through the same vision extraction as the other photos (see Phase 2b) — it's just captured via a different UI control, not through the video guide
  - If the user opts out, proceed with exterior photos only — this is a normal, accepted path, not an error or a "needs more info" case
- [ ] **Submit** button uploads to the backend: the auto-snapped exterior photos + the optional interior photo (if provided) + the full session video (stored as-is, not processed for pricing). Also send whether the user was offered/chose to include an interior photo (see Phase 5 `interior_included`) — the backend can't infer this from an unlabeled photo list on its own

### 2b. Vision extraction
- [x] Write the structured-extraction prompt for the vision API:
  ```
  Given this image of a truck, return ONLY valid JSON:
  {
    "make": string, "model": string, "year_estimate": string (range ok),
    "trim": string, "condition": "excellent"|"good"|"fair"|"poor",
    "visible_damage": [
      {"description": string, "box": [x_min, y_min, x_max, y_max] | null}
    ],
    "tire_condition": "new"|"worn"|"bald",
    "confidence": float 0-1
  }
  ```
  `box` is normalized (0-1) image coordinates for that damage instance, or `null` if the model can't localize it — a description without a usable box should still be kept, just without a drawn box later (see 2c)
- [x] Test on 10+ sample images (mix from scraped data), iterate prompt until reliably valid + reasonably accurate
- [x] Wrap API call with JSON parsing + validation, handle malformed responses (retry once, then fallback to "unknown")
- [x] Run one extraction call per auto-snapped photo only (~3-7 calls per truck) — the stored video is never sampled into frames or fed into extraction

### 2c. Damage visualization (bounding boxes)
- [x] For each photo with at least one localized damage entry (has a non-null `box`), draw a rectangle on a copy of that photo at the box coordinates (OpenCV `cv2.rectangle`, or any equivalent — implementation is flexible) and label it with the damage description
- [x] Save each annotated photo locally to a `results/` folder in the project (e.g. `results/<submission_id>/<photo_name>_annotated.jpg`), so the team can visually review flagged damage without digging through raw JSON
- [x] This is a visualization/demo aid, not a pricing input — a missing, `null`, or inaccurate box never blocks or changes pricing (Phase 4 still only uses the damage *count* and description list, unaffected by this)
- [x] Set expectations accordingly: a general-purpose VLM's box coordinates are rougher than a purpose-built object detector — good enough to point at "roughly here," not a precise measurement, and that's fine for what this is used for

---

## Phase 3 — Multi-View Fusion

Works across any number of usable views (3-7 typical) — not tied to a fixed count or fixed named waypoints, since exactly which angles the guided session actually snapped will vary.

- [x] Make/model/year: majority vote across all extractions, or highest-confidence single view if votes are split
- [x] Condition: take the *worst* (lowest) condition score seen across views — a single damaged panel shouldn't get diluted by clean views of other panels
- [ ] Damage list: union of all damage entries seen across views, deduped by description text (case-insensitive) — if an interior photo was included (Phase 2a), it's just one more view in this same union/worst-case fusion, no special-casing needed. Each entry keeps its own `box` (or `null`) from whichever view reported it; boxes are only used for Phase 2c visualization on their source photo, never merged/reprojected across views
- [x] Tire condition: worst score seen across whichever views show the tires
- [x] Output one fused JSON per upload, same schema as single-view extraction

---

## Phase 4 — Pricing Logic

Note: nothing in this phase trains a vision model. Option A is pure arithmetic against the scraped dataset. Option B trains a small tabular regression (seconds, `sklearn`/`xgboost` `.fit()`) — not a deep learning run — on the same scraped dataset. Either way, the scraped dataset is what grounds the output price in real sales data instead of an AI-guessed number; skipping it removes the entire reason this approach avoids hallucinated prices.

### 4a. Feature encoding
- [x] Map categorical condition/tire values to numeric scores (see below)
- [x] Count damage flags — `damage_count` is just `len(visible_damage)`, unaffected by whether an individual entry has a drawn box (Phase 2c) or not

```python
condition_map = {"excellent": 1.0, "good": 0.8, "fair": 0.55, "poor": 0.3}
tire_map = {"new": 1.0, "worn": 0.6, "bald": 0.2}
```

### 4b. Option A — Hand-tuned formula (BUILD THIS FIRST)
- [x] Look up `p_base` from `/data/base_prices.json` by (make, model, year)
- [x] Apply formula:
  ```
  price = p_base * (0.5 + 0.3*condition_score + 0.15*tire_score) * (1 - 0.05*damage_count)
  ```
- [x] Sanity-check output against 3-5 known real listings, adjust coefficients if wildly off
- [x] **Output a price range (e.g. ±15%) plus a confidence score — this is the headline result, not a single point price.** A point estimate may be computed internally to derive the range, but it is not the primary field surfaced to the user. Widen the range (and/or lower the confidence score) when extraction confidence is low
- [ ] **Interior-photo range adjustment**: when no interior photo was provided (Phase 2a opt-out), widen the range *asymmetrically* — lower the low end further than usual, leave the high end closer to normal. Rationale: an unseen interior could hide damage that lowers value, but can't retroactively add value, so the extra uncertainty is a downside risk, not a symmetric unknown. When an interior photo *was* provided and analyzed, use the normal (narrower) range from the confidence-based logic above — more real signal, tighter estimate

### 4c. Option B — Learned regression (STRETCH, only if Phase 1d comps-with-features data exists)
- [ ] Fit linear regression or small XGBoost on scraped comps: `price ~ condition_score + tire_score + damage_count + make/model/year bucket`
- [ ] Compare predictions against Option A on the same test cases
- [ ] Swap in as the default pricing function if it performs better/more sensibly — same function signature (`features_json → price`), so this is a drop-in replacement

### 4d. Fallback handling
- [x] Unknown/unrecognized make-model-year combo → fall back to a generic "truck" average price, flag low confidence
- [x] Very low extraction confidence → widen the price range, surface a warning in the UI
- [ ] No interior photo provided → widened range (Phase 4b) plus an explicit note in the response saying so (e.g. "No interior photo was provided, so this range is wider than usual") — this is a "priced, but here's why the range is wide" note, not a warning/error, and the frontend (Phase 6) must surface it in the results view

### 4e. Knowing its limits (required per challenge brief, not just error handling)
- [x] **Not-a-truck detection**: if the VLM extraction indicates the subject isn't a truck (or confidence is near-zero on make/model), refuse to output a price — return a clear "this doesn't look like a truck" response instead of a number
- [x] **Missing-critical-view detection**: photos aren't labeled by angle (capture is unstructured/guided, not a fixed named-waypoint checklist — see Phase 2a), so this has to be judged from content, not from a slot being empty: e.g. if none of the submitted photos give a usable read on tire condition, don't silently guess — flag exactly what's missing, matching the brief's own example ("send me a shot of the tires")
- [x] **Blurry/unusable image detection**: reuse the blur-detection heuristic (Laplacian variance) per submitted photo; below threshold → drop that photo from the usable set rather than feeding a bad extraction into fusion
- [x] **Confidence-gated response tiers**: define at least two response modes — "priced" (normal output) vs. "needs more info" (names the specific gap) — the UI (Phase 6) needs to render both, not just the happy path
- [ ] This phase directly maps to judging criterion "Does it know its limits?" — treat it as core scope, not a stretch goal, and make sure at least one demo test case (Phase 7) deliberately triggers it

---

## Phase 5 — Backend API

- [ ] `POST /predict` — accepts a submission from one guided capture session: the auto-snapped exterior photos (3-7 typical), an optional interior/cabin photo, an explicit `interior_included: bool` flag (Phase 2a — the backend can't infer this from an unlabeled photo list), and the one session video. Runs the pipeline on the photos (exterior + interior, if present) only; stores the video as-is (e.g. to disk/blob storage) as an authenticity record — not processed now, but kept for a possible future forgery/liveness check. Also triggers Phase 2c: annotated (boxed) copies of any photo with localized damage are saved to `results/<submission_id>/`, as a local side effect — not returned in the response body. Returns a **price range + confidence score as the headline** (not a single point price):
  ```json
  {
    "status": "priced",
    "price_range": [16172, 21880],
    "confidence": 0.72,
    "notes": ["No interior photo was provided, so this range is wider than usual."],
    "breakdown": {
      "base_price": 28000,
      "make": "Ford", "model": "F-150", "year_estimate": "2018-2020",
      "condition": "fair",
      "damage": ["rust on rear fender", "cracked side mirror"],
      "tire_condition": "worn",
      "views_used": 5,
      "interior_included": false
    }
  }
  ```
  `breakdown.damage` stays a plain list of description strings here — the API response doesn't need to carry box coordinates or image paths, those live only in the local `results/` artifact above. `notes` is a list of human-readable, non-error explanations for anything about this specific result the user should know (currently just the interior-range case, but built as a list so more can be added later without a shape change) — `notes` is empty/omitted when there's nothing to flag
- [x] Error handling: invalid file type, API timeout/rate limit
- [x] Wire the "needs more info" response tier from Phase 4e as a first-class API response shape (not an HTTP error) — e.g.:
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
- [ ] After Stop, before Submit: interior/cabin toggle (see Phase 2a) — a plain single-image upload control appears only when toggled on, separate from the live guided-capture UI and never part of the recorded video
- [ ] Submit sends the exterior photos, the optional interior photo, the `interior_included` flag, and the session video to the backend (Phase 5) in one request
- [ ] Loading state after submit (show progress if possible)
- [ ] Results view: headline is the **price range + confidence score** (not a single number), then the explainable breakdown (base price → condition → final), matching the "why this price" demo story
- [ ] Surface `notes` from the response prominently near the range — e.g. when no interior photo was included, the wider range should visibly say why, not just look unexplained
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
- Interior/cab photos are optional, offered via a post-capture toggle (Phase 2a) — never required, never a "needs more info" refusal if skipped, but when included they're a real pricing input (Phase 3 fusion, Phase 4b range) that narrows the estimate. Skipping it is an accepted outcome with a wider, explicitly-explained range, not a degraded/error outcome
- The session video is stored (disk/blob storage — mechanism TBD) purely as an authenticity record; no forgery/liveness detection is built against it in this pass, that's explicitly deferred to later
- Capture guidance is intentionally loose about exact angles (see Phase 2a) — don't design the pipeline or the demo around an assumption that photos arrive in a fixed order or fixed named set
- `results/` (Phase 2c annotated damage images) is a generated-artifact folder like `backend/uploads/` — gitignore it, don't commit its contents
- Damage bounding boxes (Phase 2c) are a demo/explainability aid, not a measurement — implementation is flexible (OpenCV or otherwise), and box accuracy has no bearing on pricing correctness
