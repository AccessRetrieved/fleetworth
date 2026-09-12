# Fleetworth live truck capture

Fleetworth is a 54 Hackathon FA26 prototype that estimates a used truck's price from visual evidence. This directory owns Phase 2a: one guided, live truck walkaround before the vision-extraction and pricing stages run.

## What this frontend does

The user opens the live camera, starts a continuous recording session, then walks around the truck with a loose on-screen guide: wide view, front, sides, rear, tires, and any other useful exterior view. These prompts improve coverage but are not rigid named capture slots.

During the recording, the app collects **3–7 JPEG photos**. Those photos are the only visual inputs for future extraction and pricing. The continuous session video is retained separately as a live-capture/authenticity record; it is not analysed for the price.

COCO-SSD runs locally in TensorFlow.js to check broad framing. It accepts `truck`, `car`, or `bus` detections because pickup trucks are not classified consistently. It warns when the vehicle is absent, clipped, too small, poorly lit, or blurry. When the detector cannot load, auto-snap is disabled and the user can manually snap frames after local sharpness and lighting checks.

Captured media stays in browser memory until the user submits. `window.FleetworthCapture.buildFormData()` returns a multipart package only after the session video is finalized and at least three photos are present:

- `manifest`: `manifest.json` with photo metadata and video metadata
- `photos`: repeated JPEG fields, named `capture-<n>.jpg`
- `session_video`: the original browser-recorded session video

With no API endpoint configured, Submit produces an in-browser “Evidence package ready” handoff state and dispatches the `fleetworth:capture-ready` event. Set `data-predict-endpoint` on the page body when FastAPI is ready; the app will POST the same form data to it.

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

Per the hackathon organizer's latest direction, internal appearance and damage are out of scope. Do not add interior capture, interior-condition checks, or internal-damage scoring unless that direction changes.
