# Camera Preview

A simple Flask webpage that requests webcam access when it opens and shows a live preview.
Use the Webcam dropdown to choose an available camera; changing it while the preview is open
switches the stream. It includes Start, Stop, and Submit controls; this version does not record,
upload, or store video.

## Run locally

```bash
cd frontend
python3 -m pip install -r requirements.txt
python3 app.py
```

Then visit `http://127.0.0.1:5000`.
