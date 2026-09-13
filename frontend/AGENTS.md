# Fleetworth frontend guide

## Product context

Fleetworth estimates a used truck's value from visual evidence without requiring mileage, VIN, or manual vehicle details. It is a 54 Hackathon FA26 project for the Supply Chain & Automation track.

The planned pipeline is:

1. Guided live capture
2. Vision API extraction into structured facts
3. Multi-view fusion
4. Numeric feature encoding
5. Pricing grounded in scraped TruckPaper comparables
6. Price range and explainable breakdown

The vision model extracts observable facts only; it must never generate the price. Pricing belongs to comps-backed logic or a small tabular model. Scraper and backend work belong to other team members.

## Current scope: capture + upload

The frontend supports three evidence paths:

1. **Live walkaround** (default) — camera session with auto-snapped photos + authenticity video.
2. **Upload photos** — user selects exterior JPEGs/PNGs (3–7 recommended) and reviews before estimate.
3. **Upload video** — user selects a walkaround video; the backend samples frames, drops unusable/blurry ones, picks diverse key frames, then prices from those stills.

Interior/cab photos remain out of scope. Do not require VIN, mileage, make, model, year, or condition entry.

- The app opens on a **camera-ready** screen with a live preview, Fleetworth branding, and **Start walkaround**. Denied camera permission is a recovery state with **Try again**, not a file-upload fallback.
- **Start walkaround** begins a browser `MediaRecorder` session video and enables capture. **Pause/Resume** remains available; paused time is excluded from the recorded duration. Disable pause when `MediaRecorder.pause` is unavailable.
- Active capture is a dark, full-bleed camera experience: status (Capturing, elapsed time, view count, qualitative coverage), a lightweight guidance card, auto-snap feedback, a thumbnail strip, and a visible **Stop** control. Do not use a rigid named-angle checklist or “suggestion 1 of N” stepper.
- Guidance is flexible copy that advances after a photo is saved. Coverage language is qualitative: Just started / Building coverage / Good exterior coverage / Strong coverage.
- While recording, auto-snap may collect clear frames and the user may tap the shutter. Keep a 3-photo submission floor and 7-photo ceiling internally, but do not present those numbers as a mandatory checklist. Leave enough auto-capture delay for the user to walk to a meaningfully different view.
- **Stop** finalizes the recording and opens **Review your walkaround**. The video is a live-capture/authenticity record and must not be used by pricing or visual extraction.
- Review shows photo states, a coverage summary, **Estimate truck value**, **Continue capturing** (or **Add photos** for uploads), and centered **Start over**. Photo selections accumulate up to seven. No VIN, mileage, make/model, or condition fields.
- **Submit** POSTs the photos and session video. Render loading (“Building your appraisal”), `priced` (range + confidence label, never a single point price), `needs_more_info` (amber recovery, not an error), `not_a_truck` (unable to assess), and distinct technical errors.

## Browser-side evidence rules

COCO-SSD runs locally in TensorFlow.js as a framing aid. Accept `truck`, `car`, and `bus`, since pickup trucks are inconsistently classified. It may warn about no vehicle, clipped framing, scale, lighting, or blur; never portray these heuristic checks as authoritative identification.

Treat camera acquisition separately from preview playback. Safari may reject a redundant `video.play()` call while the underlying live stream is already rendering; never cover a live preview with a camera-open error. If switching devices fails while another stream is live, retain that stream and explain the fallback without blocking capture.

If the detector fails to load, disable auto-snap and preserve a clearly labelled manual fallback based on local sharpness and lighting checks. Do not silently claim object detection succeeded.

Photos and video stay in browser memory until intentional submission. Replacing/removing/resetting photos must revoke their object URLs. Reset and page exit must stop camera tracks, stop recording where possible, and revoke all stored URLs.

## Backend handoff

`window.FleetworthCapture` in `static/app.js` exposes:

- `getManifest()` for metadata once the session is ready
- `buildFormData()` which returns `{ formData, manifest }` once there are at least three photos and a finalized video
- `showResponse(response)` to render backend response states

`buildFormData()` produces multipart fields:

- `manifest`: JSON file with photo and video metadata
- `photos`: repeated JPEG fields named `capture-<n>.jpg` (matches the backend's `photos` parameter)
- `video`: original session recording (matches the backend's `video` parameter in `backend/main.py`)

The page body's `data-predict-endpoint` identifies the FastAPI `POST /predict` route (default `http://127.0.0.1:8000/predict`). The frontend POSTs to it if present; otherwise it dispatches `fleetworth:capture-ready` and shows an evidence-ready state. Expected response shapes include:

```json
{ "status": "priced", "price_range": [22000, 26000], "confidence": 0.78, "notes": [], "breakdown": {} }
```

or:

```json
{ "status": "needs_more_info", "message": "Show a clearer view of the tires." }
```

## File map

- `app.py`: minimal Flask server with `GET /`.
- `templates/index.html`: live capture markup and pinned TensorFlow.js/COCO-SSD scripts.
- `static/app.js`: camera selection, recorder lifecycle, framing assistant, flexible photo capture, review, payload creation, and result states.
- `static/styles.css`: responsive live-capture UI.
- `requirements.txt`: Flask version range.
- `README.md`: setup, behavior, limitations, and backend handoff.

## Local development

From the repository root:

```bash
cd frontend
python3 -m pip install -r requirements.txt
python3 app.py
```

Open `http://127.0.0.1:5000`. Camera access is available on localhost. Safari is preferred for testing macOS Continuity Camera because Chrome may expose only the built-in Mac camera.

Before handing off changes:

```bash
node --check static/app.js
python3 -m py_compile app.py
```

Also test the route with Flask's test client, run `git diff --check`, and visually inspect desktop and narrow/mobile layouts. When hardware is available, verify permission denial, camera selection, session start/stop, auto-snap/manual snap, photo removal, reset, and the final multipart payload.

## Change rules

- Keep vision extraction and pricing separate; this frontend only selects evidence.
- Live capture remains session-based, with no fixed-angle gating. The user-authorized photo upload path allows adding photos on review and submitting without a video. Preserve existing photos during coverage recovery; a deliberate new capture/reset still discards them.
- If local detection misses a vehicle in a sharp, well-lit frame, allow an explicitly labelled manual shutter. Auto-snap still requires detection; backend subject and coverage gates still apply.
- Preserve the 3-photo minimum and 7-photo ceiling; an incomplete evidence package must not submit.
- The video remains an authenticity record only, never a pricing input.
- Prefer a precise corrective prompt over accepting a blurry, clipped, non-vehicle frame.
- Keep controls keyboard accessible and announce state changes through live regions.
- Preserve usable desktop and mobile layouts.
- Avoid build tooling unless the project actually needs it; the Flask/static setup is deliberately lightweight for the hackathon.
