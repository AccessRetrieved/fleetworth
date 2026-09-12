# Project guide

## Purpose

This is a minimal Flask application that serves a browser-only webcam preview. It does not record,
upload, or persist video; the Submit control is intentionally a placeholder.

## File map

- `app.py` defines the Flask app and its only route, `GET /`.
- `templates/index.html` contains the camera preview UI and loads the static assets with Flask's
  `url_for` helper.
- `static/app.js` owns camera permissions, available-camera discovery, stream lifecycle, camera
  switching, and button/status behavior. Webcam access must remain client-side through the
  browser MediaDevices API.
- `static/styles.css` contains the self-contained responsive visual styling.
- `requirements.txt` pins the Flask major-version range.

## Local development

Create or activate a Python environment, then run:

```bash
cd frontend
python3 -m pip install -r requirements.txt
python3 app.py
```

Open `http://127.0.0.1:5000`. A browser must grant camera permission before device labels can be
shown. Test with multiple webcams when changing selection behavior, and confirm Stop releases the
active media tracks.

## Change guidelines

- Preserve the privacy boundary: do not send camera data to the server unless that behavior is
  explicitly requested and documented.
- Stop superseded streams before discarding them, and stop the active stream during page unload.
- Keep status text accessible with the existing live regions and keep controls usable on narrow
  screens.
