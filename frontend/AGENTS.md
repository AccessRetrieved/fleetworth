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

The frontend owns one camera-only live walkaround. Do not add an upload picker or a capture-mode toggle.

- **Start session** begins a browser `MediaRecorder` session video and enables capture.
- A flexible guide suggests wide, front, side, rear, tire, and remaining exterior coverage. It is deliberately not a fixed-angle checklist.
- While recording, auto-snap may collect clear frames and the user may manually snap them. Keep 3–7 photos; the first three are the minimum, the rest improve coverage.
- **Stop session** finalizes the original recording. The video is a live-capture/authenticity record and must not be used by pricing or visual extraction.
- **Submit** sends the 3–7 photos and finalized video together. The UI must have loading, priced, error, and `needs_more_info` states.

Internal appearance and internal damage explicitly do not matter under the organizer's latest direction. Do not add cab-interior capture or any internal-condition scoring unless that direction changes.

## Browser-side evidence rules

COCO-SSD runs locally in TensorFlow.js as a framing aid. Accept `truck`, `car`, and `bus`, since pickup trucks are inconsistently classified. It may warn about no vehicle, clipped framing, scale, lighting, or blur; never portray these heuristic checks as authoritative identification.

If the detector fails to load, disable auto-snap and preserve a clearly labelled manual fallback based on local sharpness and lighting checks. Do not silently claim object detection succeeded.

Photos and video stay in browser memory until intentional submission. Replacing/removing/resetting photos must revoke their object URLs. Reset and page exit must stop camera tracks, stop recording where possible, and revoke all stored URLs.

## Backend handoff

`window.FleetworthCapture` in `static/app.js` exposes:

- `getManifest()` for metadata once the session is ready
- `buildFormData()` which returns `{ formData, manifest }` once there are at least three photos and a finalized video
- `showResponse(response)` to render backend response states

`buildFormData()` produces multipart fields:

- `manifest`: JSON file with photo and video metadata
- `photos`: repeated JPEG fields named `capture-<n>.jpg`
- `session_video`: original session recording

The page body's optional `data-predict-endpoint` identifies the FastAPI route. The frontend POSTs to it if present; otherwise it dispatches `fleetworth:capture-ready` and shows an evidence-ready state. Expected response shapes include:

```json
{ "status": "priced", "price_range": [22000, 26000], "confidence": 0.78 }
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
