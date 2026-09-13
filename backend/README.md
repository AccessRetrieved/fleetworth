# Fleetworth backend

FastAPI service behind `POST /predict`. See `PLAN.md` at the repo root for the full design.

## Run

```bash
uv sync
uv run uvicorn main:app --reload
```

Needs `OPENAI_API_KEY` in the repo-root `.env`.

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

## Tests

```bash
uv run pytest
```

The tests stub out OpenAI and DINOv2, so they need no API key, model download, or built index.
