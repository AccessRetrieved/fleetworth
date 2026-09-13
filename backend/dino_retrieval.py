"""
Phase 1e + 2b-DINO — DINOv2 visual retrieval.

Turns a truck photo into a pretrained DINOv2 embedding and searches a local
FAISS index of scraped TruckPaper comp images for the most visually similar
real listings. DINOv2 is used as-is (no fine-tuning): its only job is
image -> embedding -> nearest real comps. It never produces a price itself;
pricing from the retrieved comps happens in pricing.visual_base_price and
pricing_formula.compute_price.

Index files (written by build_dino_index.py) live in data/dino_index/:
  comps.faiss      — IndexFlatIP over L2-normalized embeddings, so the inner
                     product search score IS cosine similarity
  comps_meta.json  — {"model", "dim", "count", "items"}; items[i] is the
                     listing metadata for index row i

torch and faiss are imported lazily so the rest of the backend (and its
tests) can import this module without loading either.
"""
import io
import json
import warnings
from functools import lru_cache
from pathlib import Path

import numpy as np
import requests
from PIL import Image, ImageOps

MODEL_REPO = "facebookresearch/dinov2"
MODEL_NAME = "dinov2_vitb14"
METHOD = "DINOv2 ViT-B/14 + FAISS cosine retrieval"

INDEX_DIR = Path(__file__).resolve().parent.parent / "data" / "dino_index"
INDEX_FILE = "comps.faiss"
META_FILE = "comps_meta.json"

TOP_K = 20  # neighbors retrieved per query photo (PLAN.md 2b-DINO)
EMBED_BATCH_SIZE = 16

# DINOv2's standard eval transform: resize shorter side to 256, center-crop
# 224 (a multiple of the ViT-B/14 patch size), ImageNet normalization.
RESIZE_SHORT_SIDE = 256
CROP_SIZE = 224
IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


class RetrievalUnavailable(Exception):
    """The DINO path can't run (index not built, model/deps missing). The
    pipeline treats this as "no visual signal", never as a failed request."""


def _load_image(image: str | bytes | Path) -> Image.Image:
    """Accepts raw bytes, a URL, or a local path — same inputs as
    vision_extract.extract_from_image."""
    if isinstance(image, bytes):
        img = Image.open(io.BytesIO(image))
    elif isinstance(image, str) and image.startswith(("http://", "https://")):
        response = requests.get(image, timeout=30)
        response.raise_for_status()
        img = Image.open(io.BytesIO(response.content))
    else:
        img = Image.open(image)
    return ImageOps.exif_transpose(img).convert("RGB")


def preprocess(image: str | bytes | Path) -> np.ndarray:
    """Image -> float32 array of shape (3, CROP_SIZE, CROP_SIZE)."""
    img = _load_image(image)
    width, height = img.size
    scale = RESIZE_SHORT_SIDE / min(width, height)
    new_size = (max(CROP_SIZE, round(width * scale)), max(CROP_SIZE, round(height * scale)))
    img = img.resize(new_size, Image.Resampling.BICUBIC)

    left = (new_size[0] - CROP_SIZE) // 2
    top = (new_size[1] - CROP_SIZE) // 2
    img = img.crop((left, top, left + CROP_SIZE, top + CROP_SIZE))

    arr = np.asarray(img, dtype=np.float32) / 255.0
    arr = (arr - IMAGENET_MEAN) / IMAGENET_STD
    return arr.transpose(2, 0, 1)


def l2_normalize(vectors: np.ndarray) -> np.ndarray:
    vectors = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / np.maximum(norms, 1e-12)


def _best_device():
    """Fastest available torch device: CUDA > Apple Silicon MPS > CPU.
    Embedding-only (no training), so this is a pure speed choice."""
    import torch

    if torch.cuda.is_available():
        return torch.device("cuda")
    if getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


@lru_cache(maxsize=1)
def load_model():
    """Pretrained DINOv2 ViT-B/14 from torch.hub (weights are cached under
    ~/.cache/torch/hub after the first download). Eval mode, never trained.
    Moved to the fastest available device (CUDA/MPS/CPU) since this is
    forward-pass-only inference -- no training-specific device handling
    needed."""
    try:
        import torch
    except ImportError as e:
        raise RetrievalUnavailable(f"torch is not installed: {e}") from e

    try:
        with warnings.catch_warnings():
            # dinov2 warns once per layer type when xFormers isn't installed;
            # plain PyTorch attention is fine for inference.
            warnings.filterwarnings("ignore", message="xFormers is not available")
            model = torch.hub.load(MODEL_REPO, MODEL_NAME, trust_repo=True)
    except Exception as e:
        raise RetrievalUnavailable(f"couldn't load {MODEL_NAME}: {e}") from e
    model.eval()
    model.to(_best_device())
    return model


def embed_images(images: list, model=None) -> np.ndarray:
    """Images -> (n, dim) float32 array of L2-normalized DINOv2 CLS
    embeddings. `model` is injectable for tests; defaults to load_model()."""
    import torch

    if not images:
        return np.zeros((0, 0), dtype=np.float32)
    model = model if model is not None else load_model()
    try:
        device = next(model.parameters()).device
    except (AttributeError, StopIteration):
        # test doubles (e.g. StubModel) have no .parameters() / device to move to
        device = torch.device("cpu")

    batches = []
    for start in range(0, len(images), EMBED_BATCH_SIZE):
        chunk = np.stack([preprocess(img) for img in images[start:start + EMBED_BATCH_SIZE]])
        with torch.inference_mode():
            output = model(torch.from_numpy(chunk).to(device))
        batches.append(output.float().cpu().numpy())
    return l2_normalize(np.concatenate(batches))


def build_index(embeddings: np.ndarray):
    """FAISS IndexFlatIP over L2-normalized vectors (exact cosine search —
    plenty fast at hackathon scale)."""
    import faiss

    vectors = np.ascontiguousarray(l2_normalize(embeddings))
    index = faiss.IndexFlatIP(vectors.shape[1])
    index.add(vectors)
    return index


INDEX_PART_BYTES = 90_000_000  # stay safely under GitHub's 100MB hard push limit


def _index_part_paths(index_path: Path, parts: int) -> list[Path]:
    """<stem>.faiss, then <stem>.part01.faiss, <stem>.part02.faiss, ..."""
    return [index_path] + [index_path.with_name(f"{index_path.stem}.part{k:02d}{index_path.suffix}") for k in range(1, parts)]


def save_index(index, items: list[dict], index_dir: Path = INDEX_DIR, model_name: str = MODEL_NAME) -> None:
    """items[i] must describe index row i (listing_id, price, make, ...)."""
    import faiss

    if index.ntotal != len(items):
        raise ValueError(f"index has {index.ntotal} vectors but {len(items)} metadata items")
    index_dir = Path(index_dir)
    index_dir.mkdir(parents=True, exist_ok=True)
    # serialize_index rather than faiss.write_index, which goes through C
    # fopen and is fussy about non-ASCII Windows paths. Raw bytes are split
    # into <90MB parts so no single committed file trips GitHub's 100MB
    # hard push limit -- the combined index for this hackathon's dataset
    # size comfortably exceeds that as one file. load_index reassembles by
    # concatenation, since this is just a byte stream, not a structured
    # per-row format like the shard .npy files.
    index_path = index_dir / INDEX_FILE
    for stale in [index_path, *index_dir.glob(f"{index_path.stem}.part*{index_path.suffix}")]:
        stale.unlink(missing_ok=True)
    raw = faiss.serialize_index(index).tobytes()
    parts = max(1, -(-len(raw) // INDEX_PART_BYTES))
    for k, path in enumerate(_index_part_paths(index_path, parts)):
        path.write_bytes(raw[k * INDEX_PART_BYTES:(k + 1) * INDEX_PART_BYTES])
    meta = {"model": model_name, "dim": index.d, "count": index.ntotal, "index_parts": parts, "items": items}
    (index_dir / META_FILE).write_text(json.dumps(meta, indent=1), encoding="utf-8")


@lru_cache(maxsize=4)
def load_index(index_dir: Path = INDEX_DIR):
    """Returns (faiss index, metadata dict). Cached per directory."""
    index_dir = Path(index_dir)
    index_path, meta_path = index_dir / INDEX_FILE, index_dir / META_FILE
    if not index_path.exists() or not meta_path.exists():
        raise RetrievalUnavailable(
            f"DINO comp index not found in {index_dir} — run `uv run python build_dino_index.py`"
        )
    try:
        import faiss
    except ImportError as e:
        raise RetrievalUnavailable(f"faiss is not installed: {e}") from e

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    parts = meta.get("index_parts", 1)  # absent means an old single-file index, pre-split
    raw = b"".join(p.read_bytes() for p in _index_part_paths(index_path, parts))
    index = faiss.deserialize_index(np.frombuffer(raw, dtype=np.uint8))
    if index.ntotal != len(meta["items"]):
        raise RetrievalUnavailable(
            f"DINO index ({index.ntotal} vectors) and metadata ({len(meta['items'])} items) are out of sync — rebuild the index"
        )
    return index, meta


def search(query_embeddings: np.ndarray, k: int = TOP_K, index_dir: Path = INDEX_DIR) -> list[list[dict]]:
    """One neighbor list per query row, most similar first. Each neighbor is
    that comp's metadata plus its cosine `similarity` to the query."""
    index, meta = load_index(index_dir)
    k = min(k, index.ntotal)
    queries = np.ascontiguousarray(l2_normalize(query_embeddings))
    similarities, rows = index.search(queries, k)

    results = []
    for view_similarities, view_rows in zip(similarities, rows):
        results.append([
            {**meta["items"][row], "similarity": float(similarity)}
            for similarity, row in zip(view_similarities, view_rows)
            if row >= 0
        ])
    return results


def retrieve_per_view(photos: list, k: int = TOP_K, index_dir: Path = INDEX_DIR, model=None) -> list[list[dict]]:
    """Top-K comp neighbors for each query photo, in photo order. Kept
    independent of VLM make/model extraction (PLAN.md 2b-DINO)."""
    load_index(index_dir)  # fail fast, before loading the model, if the index isn't built
    if not photos:
        return []
    return search(embed_images(photos, model=model), k=k, index_dir=index_dir)
