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

## Current scope: Phase 2a

The frontend owns one camera-only live exterior walkaround. Do not add an upload picker or a capture-mode toggle. Interior/cab photos are out of scope (per PLAN.md): not prompted for, not uploaded, not penalized when absent.

- **Start session** begins a browser `MediaRecorder` session video and enables capture. Start, **Pause/Resume**, and the save control stay on one row.
- **Pause** suspends the recorder, freezes the session clock, and blocks auto-snap and manual snapping until resumed; paused time is excluded from the recorded duration. Disable the control when `MediaRecorder.pause` is unavailable.
- A flexible guide suggests wide, front, side, rear, tire, and remaining exterior coverage. It advances only after a photo is successfully saved, never from elapsed time. It is deliberately not a fixed-angle checklist: advancing offers the next idea and does not mark the previous suggestion verified or complete.
- The guide renders as one compact overlay on the camera preview, visible only while recording: current suggestion number, title, instruction, tip, and a progress bar of the remaining ideas. Keep it there rather than reintroducing a separate side panel, and keep it clear of the recording badge and framing status.
- On phone widths (≤640px) the guide moves out of the frame and sits above the preview, and the in-frame recording badge is hidden so the small camera view stays unobstructed; the header session state still reports recording. Both are markup-order/CSS concerns only — the guide stays a sibling of `.preview` inside `.capture-stage`, absolutely positioned over it on larger screens.
- While recording, auto-snap may collect clear frames and the user may manually snap them. Keep 3–7 photos; the first three are the minimum, the rest improve coverage. Leave enough auto-capture delay for the user to walk to a meaningfully different view.
- **Stop session** finalizes the original recording and is labelled **Save** while a session is ongoing. Show the finished recording in the review area. The video is a live-capture/authenticity record and must not be used by pricing or visual extraction.
- The page reads as three numbered steps — (1) Film Truck, (2) Review Photos, (3) Review Recording — with the number in a circle beside a short title. Keep this copy terse: no eyebrow labels, no restating that the video is separate from pricing, and no file sizes. Blocking states surface as one line with a red `!` (for example "Restart to collect enough photos", or "Unpause to collect enough photos" when paused short of the minimum).
- **Submit** sends the 3–7 exterior photos and finalized video together. The UI must have loading, priced, error, and `needs_more_info` states.

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
- Keep capture camera-only and session-based; no arbitrary upload path or fixed-angle gating.
- Preserve the 3-photo minimum and 7-photo ceiling; an incomplete evidence package must not submit.
- The video remains an authenticity record only, never a pricing input.
- Prefer a precise corrective prompt over accepting a blurry, clipped, non-vehicle frame.
- Keep controls keyboard accessible and announce state changes through live regions.
- Preserve usable desktop and mobile layouts.
- Avoid build tooling unless the project actually needs it; the Flask/static setup is deliberately lightweight for the hackathon.
