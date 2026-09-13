# Code Review

**Scope:** Whole tracked repository at `/Users/jonathantran/Documents/ChatGPT/54 Hacks`, commit `525ca2ec2c8d95eebe86f00cde6157b071c9172a`: backend API, extraction, fusion, pricing, DINO retrieval/index construction, scraper/cleaner, tests, dependency manifests/locks, and consistency checks on committed data. No PR/base branch was supplied; this is a repository review, not a review limited to the latest commit. The working tree was initially clean.
**Date:** 2026-09-12
**Project:** Fleetworth estimates commercial-truck prices from exterior photos, using vision extraction and comparable listings; capture videos are stored as evidence.
**Stack:** Python 3.14+, FastAPI, OpenAI client, OpenCV/Pillow, NumPy, PyTorch/DINOv2, FAISS, Playwright, BeautifulSoup, and pytest. No frontend implementation is tracked in this checkout.
**Audience and constraints:** Repository authors; preserve the existing architecture and propose focused fixes. Source review is read-only; this report is the only repository file created or changed.

## Summary

The separation between visual extraction and listing-backed pricing is clear, and the existing tests cover useful retrieval and fallback behavior. However, extraction failures can silently change an appraisal, confidence can overstate the evidence, and a prediction blocks the server's event loop. The locked native dependency combination also reproducibly aborts the process on this Mac when FAISS searches after Torch is loaded. I would address the Major findings before treating this checkout as ready for a shared demo or deployment; the native crash must be resolved for this environment.

Validation performed:

- `PYTHONDONTWRITEBYTECODE=1 backend/.venv/bin/python -B -m pytest -q -p no:cacheprovider backend/tests/test_pipeline_visual.py backend/tests/test_visual_pricing.py`: **18 passed**.
- The full `backend/tests` run completed two tests and then aborted with exit code 134 inside FAISS search. The failing test reproduced in isolation with output capture and pytest's faulthandler disabled; see Major 1. The complete suite therefore did **not** pass.
- In-memory probes reproduced the extraction, confidence, event-loop, schema-validation, and empty-shard findings below. API file writes were mocked; no live OpenAI calls, model downloads, scraper runs, or index rebuilds were performed.
- The committed clean dataset contains 1,958 unique listings. All 538 base-price buckets match recomputed statistics from that dataset; 251 buckets contain a single listing. The committed DINO index metadata has 375 items, of which 374 appear in the current clean dataset.
- Dependency manifests and local runtime behavior were inspected; an external vulnerability-advisory audit was not performed. Live scraping, actual vision accuracy, and a pretrained DINO run were not validated.

## Critical

## Major

### 1. The locked Torch/FAISS combination aborts the process on the review environment

- **Severity:** Major
- **File and line reference:** `backend/pyproject.toml:9,18`; `backend/uv.lock:270–271,1021–1022`; `backend/dino_retrieval.py:115–125,179–185`.
- **What's wrong and why it matters:** The installed `torch==2.14.0` and `faiss-cpu==1.15.0` match the lockfile. Running `test_saved_index_round_trips_and_searches_by_cosine` on this Mac terminates Python with exit code 134 and `OMP: Error #15: Initializing libomp.dylib, but found libomp.dylib already initialized.` The native trace enters FAISS's bundled OpenMP runtime. The real retrieval path also loads Torch and then calls FAISS search in the same process. This is a process abort, so the Python exception handler intended to preserve lookup-only pricing cannot catch it; the worker dies instead. This finding is verified on this environment, not a claim that every supported platform fails.
- **Suggested fix:** Establish and lock a tested macOS-compatible Torch/FAISS installation that uses a compatible single OpenMP runtime, and document the supported installation path. Add a subprocess smoke test that exercises Torch inference followed by FAISS search, so native termination becomes an observable test failure. Do not treat suppressing the duplicate-runtime check as a verified fix.
- **Reproduction:** `PYTHONDONTWRITEBYTECODE=1 backend/.venv/bin/python -B -m pytest -q -s -p no:cacheprovider -p no:faulthandler backend/tests/test_dino_retrieval.py::test_saved_index_round_trips_and_searches_by_cosine`.

### 2. A prediction executes the entire synchronous pipeline on the async event loop

- **Severity:** Major
- **File and line reference:** `backend/main.py:35–38,59–62`; `backend/pipeline.py:73–92`; `backend/vision_extract.py:169–184`.
- **What's wrong and why it matters:** `predict` is an async route, but directly calls synchronous `run_pipeline`. That function makes sequential blocking API calls for every usable photo, then performs CPU inference and retrieval. During that call, the worker's event loop cannot serve other requests, including dispatching health checks. Slow upstream responses therefore stall unrelated users even when CPU usage is low. A probe with mocked disk writes and a 250 ms pipeline delayed a concurrently scheduled 10 ms heartbeat to approximately 307 ms.
- **Suggested fix:** Execute the synchronous pipeline through FastAPI/Starlette's thread-pool helper or a bounded worker executor; move blocking disk work off the event loop as well. Bound concurrent expensive predictions to avoid replacing event-loop starvation with unbounded inference work. Verify that a health request completes while a stubbed prediction is deliberately blocked.

### 3. Upload handling has no application size/count limits and retains videos indefinitely

- **Severity:** Major
- **File and line reference:** `backend/main.py:39–59`.
- **What's wrong and why it matters:** The endpoint enforces a minimum photo count and client-supplied MIME types, but no application maximum photo count, per-file size, or total submission size. It reads every photo and the entire video into bytes, then saves the video permanently before knowing whether the submission can be assessed. Large videos can exhaust memory; repeated rejected submissions still consume disk; excessive photo counts expand paid extraction work. Framework multipart file-count defaults do not provide an appropriate application budget for these resources. No deployment-level compensating limits are present in the repository.
- **Suggested fix:** Define maximum photos, per-photo bytes, video bytes, and total submission bytes. Stream the video in bounded chunks while enforcing the limit, and reject oversized submissions before running extraction. Set a retention/cleanup policy for stored videos and partial/failed submissions. Enforce a corresponding ingress body limit if a reverse proxy is used. Add boundary tests using mocked processing so rejected uploads incur no extraction calls or retained partial files.

### 4. Failed extractions are counted as valid views and contribute invented condition data

- **Severity:** Major
- **File and line reference:** `backend/vision_extract.py:145–159,187–194`; `backend/limits.py:52,64–79`; `backend/pipeline.py:80–92`; `backend/fusion.py:89–110`.
- **What's wrong and why it matters:** API/parse failures become ordinary dictionaries with `condition="fair"`, zero confidence, and negative truck/photo flags. Downstream code excludes only `None`, so these failures count toward the minimum usable-photo requirement and participate in condition fusion. A reproduced submission with two successful, excellent-condition views at confidence 0.9 and one synthetic rate-limit fallback returns `status="priced"`, `views_used=3`, `condition="fair"`, and confidence **0.9**. It both bypasses the three-valid-view requirement and lowers the appraisal based on a failed service call. When all three calls fail, the gate instead tells the user that the subject is not a real truck, concealing the infrastructure failure.
- **Suggested fix:** Represent extraction failure separately from an actual negative truck observation. Exclude failed records from coverage, condition, identity, and retrieval selection; return an explicit upstream/service error when failures prevent assessment. Preserve genuine non-truck observations for the subject gate. Add pipeline tests for complete failure and a mixture of valid views and failures, asserting that failures cannot alter condition or satisfy coverage.

### 5. Reported confidence and range ignore thin lookup support and adverse visual spread

- **Severity:** Major
- **File and line reference:** `backend/pricing.py:95–101,206–229`; `backend/pricing_formula.py:79–81,105–109,124–132,147–150`.
- **What's wrong and why it matters:** Every exact lookup receives `confidence="high"` regardless of its sample count. `compute_price` maps that to 1.0; `sample_size` is only displayed. Consequently a single listing can produce 99% confidence and an approximately ±10.35% range when the VLM is confident, contrary to the comment that thin buckets should reduce confidence. This affects 251 of the current 538 buckets. Additionally, when the lookup is used and its median price agrees with visual retrieval, `max(base_confidence_score, visual_confidence)` prevents a broad visual neighborhood from lowering confidence or widening the range. In a controlled probe, changing visual relative IQR from 0.0 to 1.8 lowered visual confidence from 1.0 to 0.357, but the returned confidence remained 0.99 and the range remained exactly `[37204.75, 45795.25]`. The headline therefore communicates precision unsupported by the observed comps.
- **Suggested fix:** Incorporate lookup sample support into the base-confidence calculation and propagate adverse neighborhood spread/consistency into headline uncertainty even when the lookup supplies the point estimate. Keep the formula simple, but ensure a single comp cannot imply maximal price confidence and a materially wider comparable-price distribution widens the range or lowers confidence. Add tests that vary sample count and price spread independently while holding identity and median price constant.

## Minor

### 6. An empty but present shard prevents otherwise valid shards from merging

- **Severity:** Minor
- **File and line reference:** `backend/build_dino_index.py:124–126,144–152,179–192,214`.
- **What's wrong and why it matters:** A shard with no assigned listings, or whose image downloads all fail, is saved as an array of shape `(0, 0)` with an empty metadata list. Merge appends it alongside nonempty arrays of shape `(n, 768)` and calls `np.concatenate`, which raises a dimension-mismatch `ValueError`. `--allow-missing-shards` does not help because both files exist. A mocked merge of one valid `(1, 768)` shard and one `(0, 0)` shard reproduced the failure before any index write. Thus a recoverable empty slice blocks use of the completed work.
- **Suggested fix:** Validate each shard's row count against its metadata and skip a validated empty shard during concatenation, or persist a known embedding dimension for empty arrays. Retain the explicit error when no usable vectors remain. Test valid-plus-empty shards with and without the missing-shard option.

### 7. Extraction validation accepts malformed identity fields that crash downstream fusion

- **Severity:** Minor
- **File and line reference:** `backend/vision_extract.py:119–142`; `backend/fusion.py:46–50`; `backend/pricing.py:54–55,86–87`.
- **What's wrong and why it matters:** The parser checks field presence, enums, booleans, damage, and confidence, but never validates the types of `make`, `model`, `year_estimate`, or `trim`. For example, an otherwise valid response with `model: ["CASCADIA"]` passes `_parse_and_validate` and then raises `TypeError: unhashable type: 'list'` in fusion. Numeric identity fields can instead fail later string normalization. Because the parser already accepted the record, its intended malformed-output retry/fallback cannot handle this; a single malformed model response can fail the entire submission.
- **Suggested fix:** Validate the complete response shape, including a top-level object and string types for all identity fields, before returning an extraction. Raise `ExtractionError` for violations so the existing retry mechanism handles them, and route exhausted retries through the explicit failure handling recommended in Major 4. Add cases for list/numeric identity fields and non-object JSON.

## Positive notes

- Pricing is computed from actual listing records separately from vision extraction, with the chosen price source and comparable listings exposed in the breakdown.
- Retrieval fusion deduplicates a listing within each view before applying recurrence rewards. Tests verify that duplicate image matches cannot masquerade as multiple supporting views.
- Weighted-median pricing and weak-neighbor rejection reduce dependence on a single extreme nearest match; the tests cover an expensive outlier explicitly.
- The lookup path survives ordinary Python retrieval exceptions, with tests for both a missing index and an unexpected retrieval error. That fallback remains valuable once the native-runtime issue is resolved.
- Index saving checks vector/metadata counts, shard merging checks source hashes, and deterministic test embeddings avoid live model downloads.
- Recomputed base-price bucket statistics match the current clean dataset, and the checked-in clean listings have unique IDs and no auction flags.

## Questions for the author

- Is the smaller committed DINO index intentional? Its metadata covers 375 listings against 1,958 current clean listings, and one indexed ID is absent from the clean set. The README says to rebuild after dataset changes, while the new merge option permits intentional partial coverage. Define the expected coverage/source version so an intentional partial index can be distinguished from a stale artifact.
- Which deployment platform is the target, and does its installed Torch/FAISS pair pass a combined native smoke test? The process-abort finding above is confirmed on this Mac; another platform still needs its own verification.
- Are request quotas, upload limits, and video retention enforced outside this repository? If so, document their actual limits and ownership; the API currently provides none of those safeguards itself.
- Are parallel scraper processes against the same output still a supported workflow? `scraper/scrape_truckpaper.py:198–203,229–230,312–319` advertises it, but each process loads and rewrites the entire shared progress JSON without coordination. One process can overwrite another's progress, and a concurrent reader can encounter a partial write. Separate per-worker progress files or serialized checkpoint writes would be needed for reliable shared resume state.
- What held-out examples calibrate the headline confidence and price range? Current tests verify arithmetic and wiring, but do not establish empirical range coverage for unfamiliar trucks. This is especially relevant because make/model/year buckets omit mileage and configuration differences.
