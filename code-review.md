# Fleetworth — Full-Stack Code Review

**Scope:** frontend → backend flow (everything)  
**Focus:** correctness / UX robustness / security / performance / readability / PLAN.md design invariants  
**Branch reviewed:** `main` (as of review date)

---

## (a) Standard bugs / security / performance

### Critical — API failures masquerade as "this is not a truck"

`vision_extract._fallback_unknown()` (returned on timeouts/rate limits) hardcodes `is_truck: False`, `is_real_photo: False`, `confidence: 0.0`. Every one of those fields is a "not a truck" vote in `limits.evaluate`:

```python
not_truck_votes = sum(
    1 for e in usable
    if not e.get("is_truck", True)
    or not e.get("is_real_photo", True)
    or e.get("confidence", 1.0) < NOT_TRUCK_CONFIDENCE_THRESHOLD
)
if not_truck_votes > len(usable) / 2:
    return {"status": "not_a_truck", ...}
```

If OpenAI rate-limits or times out on 2 of 3 photos — entirely plausible mid-demo — a judge photographing a real truck is told their photo isn't a truck. A fallback record carries `_error`; `evaluate` should distinguish "extraction failed" from "extraction says not-truck" and return an error/retry tier instead. This is the single most demo-dangerous bug in the repo.

**Severity:** Critical

---

### Major — the whole server freezes during a prediction

`predict` is `async def`, but `run_pipeline` is synchronous and makes 3–7 serial OpenAI calls (plus OpenCV work) directly on the event loop. One submission blocks all other requests for the full pipeline duration (likely 15–45s). Either make `predict` a plain `def` (FastAPI threadpool) or run the pipeline in an executor — and ideally parallelize the per-photo extraction calls, which are embarrassingly parallel and dominate latency.

**Severity:** Major

---

### Major — FastAPI error detail never reaches the user

Backend errors raise `HTTPException(detail=...)`, which serializes as `{"detail": ...}`, but the frontend reads `data.message`:

```javascript
if (!response.ok) throw new Error(data.message || `The analysis service returned ${response.status}.`);
```

So "Unsupported photo type: …" and every pipeline error render as a generic status-code message. Read `data.detail || data.message`.

**Severity:** Major

---

### Minor — 502 leaks raw internal exception text

`detail=f"Pipeline error: {e}"` in `main.py`. Fine for a hackathon LAN, but log server-side and return something generic.

**Severity:** Minor

---

### Minor — unauthenticated, CORS-`*` endpoint that spends OpenAI credits

Anyone who can reach the backend can trigger up to 7 vision calls per request with unbounded photo/video sizes (`await video.read()` buffers the whole video in memory, and `uploads/` grows forever). Acceptable for the demo; don't expose it beyond localhost/LAN.

**Severity:** Minor

---

### Minor — leftover debug `print` in `pipeline.py`

Line 52 (`[dup-check]` Hamming-distance print) runs on every submission.

**Severity:** Minor

---

### Minor — real trucks with unidentifiable make can be refused as "not a truck"

The prompt tells the model to lower confidence when make/model is unknown; `evaluate` counts any `confidence < 0.3` as a not-truck vote, so an obscure-but-real truck can get the insulting refusal message rather than `needs_more_info`. The two thresholds (0.2 prompt guidance vs 0.3 vote cutoff) also overlap awkwardly.

**Severity:** Minor

---

### Minor — near-duplicate threshold of 12/64 bits risks false positives

aHash distances between genuinely different angles of the same truck (especially sky/pavement-dominated frames) can land under 12. Failure mode: rejecting a judge's legitimate photo.

**Severity:** Minor

---

### Minor — nearest-year fallback has no distance cap

In `pricing.py`, a 1998 query against a family whose only comps are 2020–2024 silently uses a 2020s price with "medium" confidence. Cap the year distance (e.g. ±4) before degrading to the all-years or make-level fallback.

**Severity:** Minor

---

### Minor — `_MODEL_FAMILY_PATTERNS` duplicated

Copy-pasted between `scraper/clean_data.py` and `backend/pricing.py`. Any addition on the scraper side that isn't mirrored silently breaks bucket lookups. Move to a shared module or into `data/` alongside the JSON.

**Severity:** Minor

---

### Minor — `notes` is dead

`pricing_formula.compute_price` initializes `notes = []` and never appends; the frontend faithfully renders nothing. PLAN 4d says low confidence should "surface a warning in the UI" — this is the intended vehicle for it.

**Severity:** Minor

---

### Minor — image bytes always declared `image/jpeg` in `_image_content`

API accepts PNG/WebP uploads. OpenAI sniffs content so it works, but it's a latent mismatch.

**Severity:** Minor

---

### Minor — no unit tests for pure logic

`fusion.py`, `limits.evaluate`, `pricing.base_price_lookup`, and `pricing_formula.compute_price` are deterministic and dependency-free — ideal test targets — yet the only test file is a manual, API-key-requiring extraction script.

**Severity:** Minor

---

### Minor — demo logistics

Predict endpoint hardcoded to `http://127.0.0.1:8000` in `index.html`; `getUserMedia` requires a secure context — capture won't work from a judge's phone over plain LAN HTTP. TF.js/COCO-SSD load from CDN; manual-snap fallback covers that.

**Severity:** Minor

---

## (b) Invariant violations (per PLAN.md / AGENTS.md)

### Major — invariant violation: single-comp buckets produce maximum confidence and the tightest price range

PLAN's core principle is "a lone number reads as false precision" and 4b requires range width to respond to comps support. But `base_price_lookup` assigns `confidence: "high"` on any exact match regardless of `sample_size`, and `pricing_formula` maps that straight to 1.0 — while its own comment claims the opposite:

```python
# Overall confidence folds in both how sure the vision extraction was
# AND how well-supported the base-price match is (Phase 4d) — a
# confident make/model read against a single-comp bucket shouldn't
# report as confidently as one backed by a dozen real listings.
base_confidence_score = BASE_MATCH_CONFIDENCE.get(base["confidence"], 0.4)
```

`sample_size` is returned but never used, and the bucket's `min`/`max` spread is ignored for range width. With 327 of 538 buckets at ≤2 comps, the most common exact-match outcome is one listing's asking price presented at the highest confidence the system can express.

**Severity:** Major (invariant violation)

---

### Major — invariant violation: the "not a truck" refusal renders as a crash

PLAN Phase 6 requires refusal states to "look deliberate (not a crash/broken page)." The backend returns `{"status": "not_a_truck", ...}`, but `renderBackendResponse` only branches on `priced` and `needs_more_info`; everything else goes to `renderErrorResult`, so the flagship "knows its limits" moment displays as "Something went wrong" with "Try submitting again" (guaranteed same refusal + more API calls). `frontend/AGENTS.md` omits `not_a_truck` from the response-shape contract.

**Severity:** Major (invariant violation)

---

### Minor — invariant violation (or stale spec): annotated damage photos returned in response body

PLAN Phase 5 says annotated boxed photos are a local side effect only — "not returned in the response body." Current code mounts `/results` and returns `annotated_damage_photos` URLs in the breakdown (deliberate commit). Update PLAN.md or treat as doc drift; note `results/` is publicly served while submission IDs are UUIDs.

**Severity:** Minor (invariant violation / stale spec)

---

### Observation — DINOv2/FAISS path unbuilt

Phases 1e, 2b-DINO, retrieval fusion, and 4b Option A are unchecked. Shipped pricing is the hand-tuned `(make, model, year)` lookup PLAN describes as "fallback and cross-check, not the only base-price source." Demo pitch about visual retrieval describes code that doesn't exist yet. Data buckets still use plain mean (median/trim not done per data-expansion notes).

**Not a code bug; scope/plan gap.**

---

## Invariants verified as upheld

- VLM never emits a price
- Session video stored, never enters pipeline
- Capture camera-only, no upload picker
- Interior views never prompted or required
- Headline output is range + confidence; point estimate in breakdown only
- Auction listings excluded; scraper keeps 2.5s delay
- `results/` and `uploads/` gitignored

---

## Data snapshot (at review time)

- ~1,958 clean listings, ~538 `(make, model_family, year)` buckets
- 327 buckets with `count ≤ 2` (sparse comps remain a accuracy risk)

---

## Verdict

**Needs changes before merge** — the API-failure→"not a truck" conflation, the unrendered `not_a_truck` state, and the single-comp/max-confidence pricing are all demo-visible; each is a small, contained fix.
