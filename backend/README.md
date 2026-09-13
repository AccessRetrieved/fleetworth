# Fleetworth backend

FastAPI service behind `POST /predict`. See `PLAN.md` at the repo root for the full design.

## Run

```bash
uv sync
uv run uvicorn main:app --reload
```

Needs `OPENAI_API_KEY` in the repo-root `.env`.

If running without `--reload`, restart uvicorn after changing backend code.

## Uploads and retention

`POST /predict` accepts 3–7 JPEG/PNG/WebP photos and an optional session video.
Photo-only uploads do not require a video. Live capture still sends the video
as verification evidence; video never enters the pricing pipeline.

Limits: 10 MiB per photo, 256 MiB per video, 326 MiB combined file content,
and 327 MiB for the entire multipart body, enforced while receiving it.
At most two predictions run concurrently per server process; excess requests
receive a retryable 503. Disk access and inference run off the async event loop.
An ingress proxy should enforce the same 327 MiB request-body ceiling.
Size errors identify the offending photo or session recording. The combined
budget accommodates a full recording and all seven photos at their limits.

Rejected/failed submissions leave no stored video or damage annotations.
Successful evidence expires after 24 hours; a startup/hourly cleanup removes
expired UUID submission directories under `uploads/` and `results/`.
Extraction-service failures return a retryable 503, never a non-truck refusal.

## DINOv2 visual retrieval index

Pricing uses two parallel base-price signals: the make/model/year bucket lookup
(`pricing.py`) and DINOv2 visual retrieval over the scraped TruckPaper comp photos
(`dino_retrieval.py`). The retrieval path needs a local FAISS index:

```bash
uv run python build_dino_index.py              # downloads comp photos to data/comp_images/, writes data/dino_index/
uv run python build_dino_index.py --sanity 10  # build, then print example neighborhoods + leave-one-out stats
uv run python build_dino_index.py --sanity-only 10
```

- The first run downloads the pretrained `dinov2_vitb14` weights (about 330 MB) into `~/.cache/torch/hub`. No fine-tuning happens.
- Rebuild the index whenever `data/truckpaper_clean.jsonl` changes.
- Without an index, `/predict` still works on the lookup path alone, and `breakdown.visual_comps.available` is `false` with a reason.
- On macOS, Torch and FAISS must not share a process with the locked native
  packages. `faiss_io_worker.py` reads/writes the existing FAISS flat-IP format
  in a fresh interpreter; cached NumPy vectors perform exact cosine search in
  the main process. Other platforms keep native FAISS search. This avoids the
  duplicate OpenMP abort without `KMP_DUPLICATE_LIB_OK` or an index rebuild.
  The [FAISS flat-index API](https://faiss.ai/cpp_api/struct/structfaiss_1_1IndexFlat.html)
  supports reconstructing the stored vectors; helper crashes/timeouts become
  an unavailable visual signal, leaving lookup pricing usable.

## Tests

```bash
uv run pytest
```

The tests stub out OpenAI and DINOv2, so they need no API key, model download, or built index.
The subprocess smoke test performs Torch inference followed by an index
round-trip and search, so a native abort is reported as a test failure.
