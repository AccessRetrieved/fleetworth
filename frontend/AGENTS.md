# Fleetworth frontend guide

## Product context

Fleetworth predicts a used truck's value from photos or video without mileage, VIN, or manual
vehicle details. It is a 54 Hackathon FA26 project for the Supply Chain & Automation track.

The full planned pipeline is:

1. Guided waypoint capture
2. Vision API extraction into structured facts
3. Multi-view fusion
4. Numeric feature encoding
5. Pricing grounded in scraped TruckPaper comparables
6. Price range and explainable breakdown

The vision model must extract observable facts only; it must not generate the price. Pricing comes
from comps-backed logic or a small tabular model. Scraper and backend work belong to other team
members and are outside this directory's current scope.

## Current scope: Phase 2a

This frontend collects seven required keyframes:

1. Front
2. Driver side, front half
3. Driver side, rear half
4. Rear
5. Passenger side, rear half
6. Passenger side, front half
7. Tires and wheels

Internal appearance and internal damage explicitly do not matter under the organizer's latest
direction. Do not add a cab-interior waypoint or internal-condition checks unless that direction
changes.

Exterior steps run a pretrained COCO-SSD detector locally in TensorFlow.js. The accepted vehicle
classes are `truck`, `car`, and `bus` because pickup trucks can be classified as cars. A keyframe is
eligible when a vehicle is detected, not clipped, large enough, adequately lit, and reasonably
sharp. The tire close-up uses lighting and sharpness checks plus human confirmation because the
general COCO model cannot validate tread coverage. Auto-capture applies only to detector-validated
exterior steps; manual capture and retakes remain available.

If TensorFlow.js or model loading fails, disable auto-capture and allow a clearly labeled manual
fallback after local frame-quality checks. Never silently claim that object detection succeeded.

## Privacy and backend boundary

Captured frames stay in browser memory as JPEG `Blob` objects and object URLs. Do not upload,
persist, or transmit them until the user completes the set and intentionally submits it. This
branch does not implement a backend.

The integration surface is `window.FleetworthCapture` in `static/app.js`:

- `getManifest()` returns metadata only after all waypoints are complete.
- `buildFormData()` returns `{ formData, manifest }` after all waypoints are complete.
- Multipart field `manifest` is a JSON file.
- Multipart field `images` repeats once per waypoint with `<waypoint_id>.jpg` filenames.
- The Finish capture button emits `fleetworth:capture-ready` with the manifest; a future FastAPI
  integration can use `buildFormData()` to POST the actual images.

Only one selected keyframe per waypoint belongs in this payload. Replacing a frame must revoke its
old object URL. Leaving or resetting the page must stop media tracks and revoke stored URLs.

## File map

- `app.py`: minimal Flask server with `GET /`.
- `templates/index.html`: guided-capture markup and pinned TensorFlow.js/COCO-SSD script tags.
- `static/app.js`: waypoint state machine, camera selection, detector loop, quality checks,
  auto-capture, retakes, review state, and payload creation.
- `static/styles.css`: responsive two-panel capture UI and mobile layouts.
- `requirements.txt`: Flask version range.
- `README.md`: human-facing setup, behavior, handoff, and limitations.

## Local development

From the repository root:

```bash
cd frontend
python3 -m pip install -r requirements.txt
python3 app.py
```

Open `http://127.0.0.1:5000`. Camera access is available on localhost. Safari is preferred for
testing macOS Continuity Camera because Chrome may expose only the built-in Mac camera.

Before handing off changes:

```bash
node --check static/app.js
python3 -m py_compile app.py
```

Also test the route with Flask's test client, run `git diff --check`, and visually inspect desktop
and narrow/mobile layouts. When hardware is available, verify permission denial, camera switching,
pausing/restarting, all seven waypoints, auto-capture, manual capture, retakes, reset, and final
payload ordering.

## Change rules

- Keep vision extraction and pricing separate; this frontend only selects input evidence.
- Keep all seven required views non-skippable so missing critical evidence blocks submission.
- Prefer explicit refusal or a precise corrective prompt over accepting a blurry, clipped,
  non-vehicle, or missing view.
- Keep camera and status controls keyboard accessible and announce state changes through live
  regions.
- Preserve usable layouts at desktop and mobile widths.
- Avoid adding build tooling unless the project actually needs it; the current Flask/static setup
  is intentionally lightweight for the hackathon.
