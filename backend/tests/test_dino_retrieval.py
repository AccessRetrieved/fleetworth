"""DINOv2 embedding + FAISS index plumbing. A stub model stands in for DINOv2
so these run without downloading weights."""
import io

import numpy as np
import pytest
import torch
from PIL import Image

from dino_retrieval import (
    CROP_SIZE,
    RetrievalUnavailable,
    build_index,
    embed_images,
    load_index,
    preprocess,
    retrieve_per_view,
    save_index,
    search,
)


def jpeg_bytes(color, size=(640, 480)):
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="JPEG")
    return buffer.getvalue()


class StubModel:
    """Per-channel mean of the normalized image — a deterministic, tiny
    'embedding' that still tells solid colors apart."""

    def __call__(self, batch: torch.Tensor) -> torch.Tensor:
        return batch.mean(dim=(2, 3))


def comp_items(n):
    return [{"listing_id": str(i), "price": 10_000.0 * (i + 1), "title": f"comp {i}"} for i in range(n)]


def test_preprocess_matches_dinov2_input_shape():
    tensor = preprocess(jpeg_bytes("red", size=(1024, 768)))
    assert tensor.shape == (3, CROP_SIZE, CROP_SIZE)
    assert tensor.dtype == np.float32


def test_embeddings_are_l2_normalized_across_batches():
    images = [jpeg_bytes(color) for color in ("red", "green", "blue")] * 7  # 21 > EMBED_BATCH_SIZE
    embeddings = embed_images(images, model=StubModel())

    assert embeddings.shape == (21, 3)
    np.testing.assert_allclose(np.linalg.norm(embeddings, axis=1), 1.0, rtol=1e-5)


def test_saved_index_round_trips_and_searches_by_cosine(tmp_path):
    vectors = np.random.default_rng(0).normal(size=(10, 8)).astype(np.float32)
    save_index(build_index(vectors), comp_items(10), index_dir=tmp_path)

    neighbors = search(vectors[3:4], k=50, index_dir=tmp_path)[0]

    assert len(neighbors) == 10  # k is clipped to the index size
    assert neighbors[0]["listing_id"] == "3"
    assert neighbors[0]["price"] == 40_000.0
    assert neighbors[0]["similarity"] == pytest.approx(1.0, abs=1e-5)
    similarities = [n["similarity"] for n in neighbors]
    assert similarities == sorted(similarities, reverse=True)


def test_save_index_rejects_mismatched_metadata(tmp_path):
    vectors = np.eye(4, dtype=np.float32)
    with pytest.raises(ValueError):
        save_index(build_index(vectors), comp_items(3), index_dir=tmp_path)


def test_missing_index_raises_retrieval_unavailable(tmp_path):
    with pytest.raises(RetrievalUnavailable):
        load_index(tmp_path / "not_built")


def test_retrieve_per_view_finds_the_visually_matching_comp(tmp_path):
    model = StubModel()
    comp_photos = [jpeg_bytes(color) for color in ("red", "green", "blue")]
    save_index(build_index(embed_images(comp_photos, model=model)), comp_items(3), index_dir=tmp_path)

    per_view = retrieve_per_view(
        [jpeg_bytes("green"), jpeg_bytes("blue", size=(480, 640))], k=2, index_dir=tmp_path, model=model
    )

    assert [view[0]["listing_id"] for view in per_view] == ["1", "2"]
    assert all(len(view) == 2 for view in per_view)
