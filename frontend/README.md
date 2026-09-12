# Fleetworth guided capture

Fleetworth is a 54 Hackathon FA26 prototype that estimates a used truck's price from visual evidence.
This directory owns Phase 2a: collecting a consistent, reviewable set of truck images before the
vision-extraction and pricing stages run.

## What this frontend does

The browser walks the user through seven required waypoints:

1. Front
2. Driver side, front half
3. Driver side, rear half
4. Rear
5. Passenger side, rear half
6. Passenger side, front half
7. Tires and wheels

For exterior views, a pretrained COCO-SSD model runs locally in TensorFlow.js and accepts `truck`,
`car`, or `bus` detections because pickup trucks are not always classified consistently. It checks
that a vehicle is present, large enough, not clipped by the frame, and reasonably sharp and lit.
The tire step uses sharpness and lighting checks, then asks the user to confirm the required
subject. Auto-capture is available for validated exterior views; every view can also be
captured manually and retaken from the inspection route or review grid.

Captured JPEGs are held in browser memory as object URLs. Nothing is uploaded or persisted. Once
all seven views exist, `window.FleetworthCapture.buildFormData()` returns a multipart `FormData`
payload with:

- `manifest`: `manifest.json`, including waypoint IDs, dimensions, timestamps, and detector scores
- `images`: one JPEG per waypoint, named `<waypoint_id>.jpg`

The Finish capture button currently emits a `fleetworth:capture-ready` browser event with the
manifest. A future backend integration can call the same public helper and POST its returned form
data to FastAPI.

## Run locally

From the repository root:

```bash
cd frontend
python3 -m pip install -r requirements.txt
python3 app.py
```

Open `http://127.0.0.1:5000`. Camera APIs work on localhost. Allow camera access when prompted.
Safari is the most reliable choice for macOS Continuity Camera; Chrome may not expose an iPhone as
a browser video input even when FaceTime can use it.

The TensorFlow.js and COCO-SSD scripts are pinned and loaded from jsDelivr, while the model weights
are fetched by COCO-SSD. If they cannot load, the app disables auto-capture and falls back to manual
capture after local sharpness and lighting checks.

## Current boundary

This frontend intentionally does not call a vision API, infer price, save images, or implement a
backend route. Its output is the selected keyframe set needed by the next pipeline stage.

Per the hackathon organizer's latest direction, internal appearance and damage are out of scope.
Do not add a cab-interior waypoint or internal-condition scoring unless that requirement changes.
