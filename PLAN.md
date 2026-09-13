# Fleetworth — Build Plan

Webapp that predicts a used truck's price from a photo or video alone — no mileage, no VIN, no manual input. Built for 54 Hackathon FA26 (Supply Chain & Automation track, sponsored by Kamion).

## Status (as of 2026-09-13)

Backend pipeline (Phases 1–5) is complete, tested, and merged into `main`:
scraper → 67,563 raw listings → 58,723 cleaned → 57,809 DINOv2-embedded
comps; VLM extraction (`gpt-4o-mini`, validated against `gpt-4o` — mini
won on model-family/year accuracy at ~1/17th the cost, see
`backend/eval_vlm_accuracy.py`); fused VLM+DINO pricing formula tuned via
leave-one-out sanity checks (dataset-median baseline 47.2% → 13.2% median
abs. % error with the shipped formula, `backend/build_dino_index.py
--sanity-only`). 24/24 backend tests passing.

Frontend (Phase 6) exists and works end-to-end against the current
backend (live camera capture, guide overlay, auto-snap, results/needs-more-
info/error views, annotated damage photos) — currently evaluating a second
design (`UIv2` branch) against it before picking one for the demo.

Remaining: Phase 7 (demo prep) hasn't been started — this is the actual
risk to close out, not further engineering. Phases 4c and 6b are optional
stretch goals, not required.

## Architecture

```
Guided Live Capture — camera opens on page load, one continuous session:
  a guide walks the user around the truck while photos auto-snap along
  the way (flexible angles/count, ~3-7 typical); the whole session is
  also recorded as video, kept only as a real-life/anti-forgery record —
  the video is NOT sampled or fed into the pipeline below, only the
  snapped photos are
      │
      ├───────────────────────────────┐
      ▼                               ▼
Vision API Extraction             DINOv2 Visual Retrieval
(per photo → structured JSON:     (per photo → image embedding;
 make, model, year_estimate,       cosine search against embeddings
 trim, condition, visible_damage,  of scraped TruckPaper comp images;
 tire_condition, confidence)       retrieve Top-K visually similar trucks)
      │                               │
      ▼                               ▼
Multi-View Fusion                 Multi-View Retrieval Fusion
(majority vote for make/model/    (merge Top-K results across all query
 year; worst-case damage/wear)     views; reward comps that recur across
      │                            multiple views; keep similarity scores)
      └──────────────┬────────────────┘
                     ▼
            Pricing Feature Builder
(structured VLM features + visual-neighbor prices + DINO similarity stats)
                     │
                     ▼
Pricing Logic
  Baseline: similarity-weighted median/mean of retrieved comp prices,
            adjusted by visible condition/tire/damage
  Stretch: XGBoost/LightGBM regression over structured + retrieval features
                     │
                     ▼
Price Range + Confidence Score + Explainable Breakdown
  (show both identified attributes and the closest visual comps;
   no single point price as the headline output)
```

Design principles:
- The AI vision API extracts *what it sees* (structured facts), never the price itself. Price comes from a formula/model grounded in real scraped comps data. This is the core explainability story for the demo — the challenge brief explicitly says a thin "send photo to vision API, print the number" wrapper will lose ("We'll be able to tell"), so this separation is not optional polish, it's the core requirement.
- No vision model gets trained from scratch or fine-tuned — the VLM is used pretrained/as-is for extraction. The only "training" in this project is fitting a small tabular regression (Option B, seconds not hours) on the scraped comps dataset.
- **DINOv2 is the visual retrieval backbone, not a price predictor.** Every scraped comp image and every submitted query photo is converted into a pretrained DINOv2 embedding. Query embeddings are matched by cosine similarity in a local FAISS index (or equivalent vector store), producing visually similar real listings with real prices. This gives the system a second, non-generative pricing signal that does not require correctly naming the make/model/year first.
- **VLM extraction and DINO retrieval run in parallel and cross-check each other.** The VLM explains *what* is visible; DINO answers *which real listed trucks look most similar*. If the two disagree strongly, confidence should fall rather than silently trusting either one.
- A truck is too long to fit in one frame at useful detail, so capture is a single guided live session that auto-snaps several photos (~3-7 typical) as the user moves around the truck, instead of relying on one shot — this also keeps vision API calls to a handful per truck instead of processing a full video stream.
- **The recorded video and the snapped photos serve different, separate purposes.** Photos are the only thing that feeds the vision/pricing pipeline. The video exists purely as evidence the capture was a real live session (not a forged upload or stock photos) — it's stored as-is and not processed now. A later stretch could add automated liveness/forgery detection against the video, but that's explicitly out of scope for this build.
- **Don't over-constrain the capture guidance to fixed named angles** (e.g. exactly one "front" shot, one "driver side" shot). We don't control, and don't know in advance, what angles a judge's own unseen photos will use when they test the system directly — so the guidance during capture should encourage broad coverage (move around the truck, get the tires, etc.) without hard-requiring a rigid per-angle checklist, and the pipeline (Phase 3 fusion, Phase 4e limits) must already tolerate a variable, unlabeled set of views rather than assuming named waypoints.
- **The headline output is a price range + a confidence score, not a single point price.** A lone number reads as false precision for something priced off a handful of photos; the range and confidence are what the "why this price" explainability story is actually selling. An internal point estimate may still exist as an implementation detail (e.g. to compute the range), but it is never the primary thing shown or returned.
- **Interior/cab damage is out of scope.** Don't require or rely on an interior photo — it may not be provided at all, and internal condition/damage isn't part of this build's pricing signal. Fusion and the "missing view" check should only consider exterior views.
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

### 1e. DINOv2 comp-image embedding index
- [x] Load a pretrained DINOv2 encoder (start with `dinov2_vitb14`; no fine-tuning)
- [x] For each retained TruckPaper comp image, preprocess it with the model's standard transform and compute one normalized embedding vector
- [x] Store `{listing_id, image_id, embedding, price, make, model, year, detail_url}`; keep the embedding array separate from the raw JSONL if convenient
- [x] Build a local FAISS cosine-similarity index (`IndexFlatIP` over L2-normalized vectors is sufficient for hackathon scale)
- [x] Save the index plus a metadata mapping so a nearest-neighbor result can be turned back into the source listing and real price
- [x] Sanity-check retrieval manually: query 10 comp images and verify that nearest neighbors are at least visually/semantically plausible trucks
- [x] Do **not** train DINO on the hackathon dataset; its job is only `image → embedding → nearest real comps`

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

### 2b-DINO. Query image → embedding → visual comps
- [x] Run each usable auto-snapped query photo through the same pretrained DINOv2 encoder used in Phase 1e
- [x] L2-normalize the resulting embedding and query the FAISS index with cosine similarity
- [x] Retrieve Top-K neighbors per photo (start with `K=20`); return listing ID, similarity score, real listed price, and metadata for each neighbor
- [x] Do not copy the single nearest truck's price. Keep the neighborhood distribution so one weird match cannot dominate the estimate
- [x] For each query view, record retrieval diagnostics such as `top1_similarity`, `topK_mean_similarity`, and price spread; low similarity or extremely wide neighbor prices should lower confidence
- [x] Keep this path independent from VLM make/model extraction — a visually useful neighbor remains useful even when the VLM cannot confidently name the truck

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
- [x] Damage list: union of all damage entries seen across views, deduped by description text (case-insensitive) — exterior damage only, per the interior-out-of-scope decision above. Each entry keeps its own `box` (or `null`) from whichever view reported it; boxes are only used for Phase 2c visualization on their source photo, never merged/reprojected across views
- [x] Tire condition: worst score seen across whichever views show the tires
- [x] Output one fused JSON per upload, same schema as single-view extraction
- [x] **DINO retrieval fusion:** merge the Top-K neighbor lists from all usable query photos. Reward a comp when the same `listing_id` appears near the top for multiple different views instead of treating every image match independently
- [x] Compute a fused retrieval score, e.g. `sum(view_similarity^alpha)` with a recurrence bonus for appearing in 2+ views; keep the exact formula simple and inspectable
- [x] Keep the best ~20-50 fused comps as the visual neighborhood for pricing, together with their real prices and similarity scores
- [x] Record retrieval consistency: if front/side/rear views all point toward the same family of comps, confidence rises; if views retrieve unrelated truck types, confidence falls

---

## Phase 4 — Pricing Logic

Note: nothing in this phase trains a vision model. The VLM and DINOv2 are both pretrained/as-is. DINOv2 supplies a visual-neighborhood price prior from real scraped comps; the VLM supplies interpretable attributes and condition signals. Any learned model here is only a small tabular regressor over those derived features.

### 4a. Feature encoding
- [x] Map categorical condition/tire values to numeric scores (see below)
- [x] Count damage flags — `damage_count` is just `len(visible_damage)`, unaffected by whether an individual entry has a drawn box (Phase 2c) or not

```python
condition_map = {"excellent": 1.0, "good": 0.8, "fair": 0.55, "poor": 0.3}
tire_map = {"new": 1.0, "worn": 0.6, "bald": 0.2}
```

### 4b. Option A — DINO visual-neighborhood price baseline (BUILD THIS FIRST)
- [x] Take the fused DINO Top-K comparable listings from Phase 3
- [x] Compute a robust visual-neighborhood base price, preferably a **similarity-weighted median** (weighted mean is acceptable as a first implementation):
  ```
  weight_i = max(similarity_i, 0) ** alpha
  p_visual = weighted_median(comp_price_i, weight_i)
  ```
- [x] Reject/downweight very weak visual matches below a chosen cosine-similarity threshold rather than forcing every query to use bad neighbors
- [x] Use the spread of retrieved comp prices (weighted IQR / MAD / standard deviation) as a direct uncertainty signal
- [x] Apply the existing visible-condition adjustment on top of `p_visual`:
  ```
  price = p_visual * condition_adjustment(condition_score, tire_score, damage_count)
  ```
  Keep the current hand-tuned `(make, model, year) → p_base` lookup as a fallback and as a cross-check, not the only base-price source
- [x] If DINO visual price and make/model/year bucket price agree, raise confidence; if they differ sharply, widen the range and surface the disagreement in the explanation
- [x] Sanity-check output against known real listings
- [x] **Output a price range plus a confidence score — this is the headline result, not a single point price.** Range width should respond to both VLM extraction confidence and DINO-neighborhood price spread

### 4c. Option B — Learned regression over VLM + DINO features (STRETCH)
- [ ] Fit a small XGBoost/LightGBM model on scraped comps; no vision model training
- [ ] Candidate features:
  - `p_visual` (DINO weighted-neighbor price)
  - `top1_similarity`, `topK_mean_similarity`, multi-view recurrence score
  - neighbor-price spread / IQR
  - VLM `condition_score`, `tire_score`, `damage_count`
  - VLM make/model/year bucket or encoded categorical fields
- [ ] Example target:
  ```
  price ~ p_visual + similarity_stats + condition_score
          + tire_score + damage_count + make/model/year
  ```
- [ ] Compare this model against both baselines: (1) old make/model/year lookup formula and (2) DINO weighted-neighbor pricing
- [ ] Only swap it in if held-out error improves; otherwise keep the simpler DINO baseline

### 4d. Fallback handling
- [x] Unknown/unrecognized make-model-year combo but strong DINO neighbors exist → price primarily from the DINO visual neighborhood instead of immediately falling back to a generic truck average
- [x] Unknown make/model/year **and** weak DINO neighbors → fall back to a generic "truck" average price, flag very low confidence
- [x] Very low extraction confidence → widen the price range, surface a warning in the UI
- [x] Strong disagreement between VLM identity and DINO neighborhood → keep the estimate conservative, lower confidence, and show both signals in the breakdown

### 4e. Knowing its limits (required per challenge brief, not just error handling)
- [x] **Not-a-truck detection**: if the VLM extraction indicates the subject isn't a truck (or confidence is near-zero on make/model), refuse to output a price — return a clear "this doesn't look like a truck" response instead of a number
- [x] **Missing-critical-view detection**: photos aren't labeled by angle (capture is unstructured/guided, not a fixed named-waypoint checklist — see Phase 2a), so this has to be judged from content, not from a slot being empty: e.g. if none of the submitted photos give a usable read on tire condition, don't silently guess — flag exactly what's missing, matching the brief's own example ("send me a shot of the tires")
- [x] **Blurry/unusable image detection**: reuse the blur-detection heuristic (Laplacian variance) per submitted photo; below threshold → drop that photo from the usable set rather than feeding a bad extraction into fusion
- [x] **Confidence-gated response tiers**: define at least two response modes — "priced" (normal output) vs. "needs more info" (names the specific gap) — the UI (Phase 6) needs to render both, not just the happy path
- [ ] This phase directly maps to judging criterion "Does it know its limits?" — treat it as core scope, not a stretch goal, and make sure at least one demo test case (Phase 7) deliberately triggers it

---

## Phase 5 — Backend API

- [x] `POST /predict` — accepts a submission from one guided capture session: the auto-snapped photos (3-7 typical) plus the one session video. Runs the pipeline on the photos only; stores the video as-is (e.g. to disk/blob storage) as an authenticity record — not processed now, but kept for a possible future forgery/liveness check. Also triggers Phase 2c: annotated (boxed) copies of any photo with localized damage are saved to `results/<submission_id>/`, as a local side effect — not returned in the response body. Returns a **price range + confidence score as the headline** (not a single point price):
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
      "views_used": 5,
      "visual_comps": {
        "method": "DINOv2 + FAISS cosine retrieval",
        "neighbors_used": 20,
        "visual_base_price": 26400,
        "top_similarity": 0.91
      }
    }
  }
  ```
  `breakdown.damage` stays a plain list of description strings here — the API response doesn't need to carry box coordinates or image paths, those live only in the local `results/` artifact above
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

- [x] **No upload picker, no mode choice** — the page opens straight into a live camera feed with Start / Stop / Submit controls (see Phase 2a); this replaces any drag-and-drop upload flow
- [x] Guide overlay during recording, prompting the user to move around the truck (loose guidance, not a rigid per-angle checklist — see Phase 2a)
- [x] Auto-snap indicator so the user can see photos being captured during the session
- [x] Submit sends both the snapped photos and the session video to the backend (Phase 5) in one request
- [x] Loading state after submit (show progress if possible)
- [x] Results view: headline is the **price range + confidence score** (not a single number), then the explainable breakdown (base price → condition → final), matching the "why this price" demo story
- [x] **"Needs more info" view**: distinct from an error state — this is a successful, intended outcome per the brief, so design it to look deliberate (not a crash/broken page), e.g. "we need a clearer shot of X" with a way to add the missing photo and retry
- [x] Basic error states (bad upload, API failure) — kept separate from the "needs more info" case above, since one is a system limitation being handled gracefully and the other is a genuine error
- [ ] **Open decision:** two frontend designs now exist against the same backend — `main`'s current frontend and the `UIv2` branch (which additionally has a dedicated `not_a_truck` view `main` currently lacks, but hardcodes `127.0.0.1:8000` instead of `main`'s LAN-dynamic backend origin). Pick one before the demo.

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
- [ ] Rehearse the DINO explanation in one sentence: **"We embed the judge's truck photos and retrieve the most visually similar real TruckPaper listings, so our base price comes from actual comparable vehicles rather than an LLM hallucinating a number."**
- [ ] Remember: judges bring their own unseen photos for a live appraisal — build and test for genuinely unfamiliar inputs, not just your curated demo set

---

## Notes / Open Decisions

- Vision API choice: `gpt-4o-mini` — validated against `gpt-4o` on 40 real listings (`backend/eval_vlm_accuracy.py`); mini won on model-family match (50% vs 38%) and year accuracy (52% vs 30%) at ~1/17th the per-image cost, so there's no case for the larger model on this task
- DINO stack: pretrained DINOv2 + local FAISS cosine index; start with `dinov2_vitb14` and `IndexFlatIP`, only optimize if retrieval latency becomes a real issue
- Scraper scope: if time-constrained, limit to 3-5 common truck models (F-150, Silverado, Ram 1500, etc.) rather than all trucks, for better comps density per bucket
- Legal/ethical note: scrape respectfully (rate limits, robots.txt, no auth bypass) — this is a hackathon demo, not a production scraping operation
- In-browser detection library (Phase 2a): if TensorFlow.js/YOLO setup for the auto-snap trigger eats too much time, a simpler fallback (snap on a fixed timer instead of framing-based detection) is an acceptable substitute — don't let this polish block the core pipeline
- Interior/cab photos are explicitly out of scope for this build (not required, not penalized if missing, not fused/priced on) — we don't know if interior images will even be provided, and internal damage isn't part of the pricing signal here
- The session video is stored (disk/blob storage — mechanism TBD) purely as an authenticity record; no forgery/liveness detection is built against it in this pass, that's explicitly deferred to later
- Capture guidance is intentionally loose about exact angles (see Phase 2a) — don't design the pipeline or the demo around an assumption that photos arrive in a fixed order or fixed named set
- `results/` (Phase 2c annotated damage images) is a generated-artifact folder like `backend/uploads/` — gitignore it, don't commit its contents
- Damage bounding boxes (Phase 2c) are a demo/explainability aid, not a measurement — implementation is flexible (OpenCV or otherwise), and box accuracy has no bearing on pricing correctness
