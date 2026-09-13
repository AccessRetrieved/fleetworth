# Fleetworth live truck capture

Fleetworth is a 54 Hackathon FA26 prototype that estimates a used truck's price from visual evidence. This directory owns Phase 2a: one guided, live truck walkaround before the vision-extraction and pricing stages run.

## What this frontend does

The user opens the live camera, starts a continuous recording session, then walks around the truck with a loose guide overlaid on the camera view: wide view, front, sides, rear, tires, and any other useful exterior view. The guide appears only while recording and offers its next suggestion only after a photo is successfully saved, so it never runs ahead while the camera is pointed away. These prompts are still unverified recommendations, not capture slots, and advancing does not claim that an angle was recognized or completed.

During the recording, the app collects **3–7 JPEG exterior photos**, waiting about six seconds of clear framing between automatic captures so the user can move. The session can be paused and resumed; paused time is excluded from the recording and no photos are snapped while paused. Exterior photos feed future extraction and pricing; interior/cab photos are out of scope. The continuous session video is retained separately as a live-capture/authenticity record, shown for review after recording, and is not analysed for the price.

COCO-SSD runs locally in TensorFlow.js to check broad framing. It accepts `truck`, `car`, or `bus` detections because pickup trucks are not classified consistently. It warns when the vehicle is absent, clipped, too small, poorly lit, or blurry. When the detector cannot load, auto-snap is disabled and the user can manually snap frames after local sharpness and lighting checks.

Captured media stays in browser memory until the user submits. `window.FleetworthCapture.buildFormData()` returns a multipart package only after the session video is finalized and at least three photos are present:

- `manifest`: `manifest.json` with photo metadata and video metadata
- `photos`: repeated exterior JPEG fields, named `capture-<n>.jpg`
- `video`: the original browser-recorded session video

`data-predict-endpoint` on the page body points at the FastAPI `POST /predict` route (default `http://127.0.0.1:8000/predict`); Submit POSTs the form data there and also dispatches the `fleetworth:capture-ready` event. If the attribute is emptied, Submit instead shows an in-browser “Evidence package ready” handoff state. Priced responses render the price range and confidence first, followed by response `notes` and the explainable breakdown. `needs_more_info` responses stay distinct from failures and lead into a focused recapture.

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
