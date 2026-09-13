# Fleetworth live truck capture

Photo uploads accumulate across selections. Use **Add photos** on review to
add more, up to seven. Three photos can submit without a session video.
**Stop** opens review immediately while the live recording finishes saving;
submission stays disabled until it finishes. Coverage recovery preserves
existing views. A missed browser detection permits explicitly labelled manual
capture only when the local lighting/sharpness checks pass.

Run interaction regressions with `node --test frontend/tests/capture.test.cjs`
from the repository root. These stub DOM/media/network boundaries and need no
camera or API key. Real-camera/visual checks remain separate.

Fleetworth is a 54 Hackathon FA26 prototype that estimates a used truck's price from visual evidence. This directory owns Phase 2a: one guided, live truck walkaround before the vision-extraction and pricing stages run.

## What this frontend does

The app opens on a live camera-ready screen. The user starts one guided walkaround: a session video records for verification while the app auto-snaps exterior photos. Guidance is loose (wider view, sides, tires, damage, hold steady) and is not a named-angle checklist. Coverage is described qualitatively rather than as a required photo count.

During recording, the app typically collects several JPEG exterior photos, waiting about six seconds of clear framing between automatic captures. The session can be paused and resumed; paused time is excluded from the recording. Exterior photos feed extraction and pricing; interior/cab photos are out of scope. The session video is an authenticity record only and is not analysed for the price.

## Visual direction

## Evidence options

- **Start walkaround** — live camera session (photos + authenticity video).
- **Upload photos** — exterior stills only; review then estimate.
- **Upload video** — backend extracts clear key frames, drops blurry/unusable ones, then prices from the selected stills.

By default the UI posts to `http://127.0.0.1:8000/predict` (`data-demo-mode="false"`).

For UI-only demos without the API, set on the page body:

```html
<body data-demo-mode="true" data-predict-endpoint="">
```

The backend needs `OPENAI_API_KEY` in a repo-root `.env` file for vision extraction.

COCO-SSD runs locally in TensorFlow.js to check broad framing. It accepts `truck`, `car`, or `bus` detections because pickup trucks are not classified consistently. It warns when the vehicle is absent, clipped, too small, poorly lit, or blurry. When the detector cannot load, auto-snap is disabled and the user can manually snap frames after local sharpness and lighting checks.

Captured media stays in browser memory until the user submits. `window.FleetworthCapture.buildFormData()` returns a multipart package only after the session video is finalized and at least three photos are present:

- `manifest`: `manifest.json` with photo metadata and video metadata
- `photos`: repeated exterior JPEG fields, named `capture-<n>.jpg`
- `video`: the original browser-recorded session video

`data-predict-endpoint` on the page body points at the FastAPI `POST /predict` route (default `http://127.0.0.1:8000/predict`); Estimate truck value POSTs the form data there and also dispatches the `fleetworth:capture-ready` event. If the attribute is emptied, submit instead shows an in-browser “Evidence package ready” handoff state. Priced responses render a range and confidence label first, then identified vehicle, visible condition, and comparable-market explanation. `needs_more_info` is an amber recovery flow, not a technical failure.

## Run locally

From the repository root:

```bash
cd frontend
python3 -m pip install -r requirements.txt
python3 app.py
```

Open `http://127.0.0.1:5000`. Camera APIs work on localhost. Allow camera access when prompted. Safari is the most reliable choice for macOS Continuity Camera; Chrome may not expose an iPhone as a browser video input even when FaceTime can use it.

The TensorFlow.js and COCO-SSD scripts are pinned and loaded from jsDelivr; model weights are fetched by COCO-SSD. If they cannot load, the app safely falls back to manual snapping rather than claiming detection succeeded.

## Current boundary

This frontend does not call a vision API, infer price, save media, or implement a backend route. It produces the session evidence package needed by the next pipeline stage.
