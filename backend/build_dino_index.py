"""
Phase 1e — build the DINOv2 comp-image index.

Downloads the primary photo of every listing in data/truckpaper_clean.jsonl
(cached under data/comp_images/, so reruns don't refetch), embeds each one
with the pretrained DINOv2 encoder, and writes the FAISS index + metadata to
data/dino_index/ for dino_retrieval.py to load.

Usage:
    uv run python build_dino_index.py                  # build the index
    uv run python build_dino_index.py --sanity 10      # build, then sanity-check retrieval
    uv run python build_dino_index.py --sanity-only 10 # sanity-check an existing index

Splitting the work across multiple machines (the download + embed steps are
independent per listing; only the final FAISS index needs to happen once):
    # on each of N machines (i = 0, 1, ..., N-1), same truckpaper_clean.jsonl:
    uv run python build_dino_index.py --shard 0/3
    uv run python build_dino_index.py --shard 1/3
    uv run python build_dino_index.py --shard 2/3
    # copy every data/dino_shards/shard_*_of_3.* file onto one machine, then:
    uv run python build_dino_index.py --merge 3
All shards must be built from the identical data/truckpaper_clean.jsonl —
the split is a deterministic slice of that file's listing order. Each
shard records a sha256 of the file it was built from, and --merge refuses
to combine shards whose hashes disagree rather than silently producing a
wrong/incomplete index.
"""
import argparse
import hashlib
import io
import json
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import requests
from PIL import Image

from dino_retrieval import INDEX_DIR, MODEL_NAME, TOP_K, build_index, embed_images, load_index, save_index, search
from fusion import fuse_retrieval
from pricing import visual_base_price

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CLEAN_PATH = DATA_DIR / "truckpaper_clean.jsonl"
IMAGE_CACHE_DIR = DATA_DIR / "comp_images"
SHARD_DIR = DATA_DIR / "dino_shards"

REQUEST_DELAY_S = 0.3  # polite gap this thread waits after its own request completes
DOWNLOAD_WORKERS = 24  # concurrent in-flight downloads; each still paces itself by REQUEST_DELAY_S
EMBED_CHUNK = 64
# Shard vectors travel through git, and GitHub rejects any file over 100 MB
# (every machine's shard can hold the whole merged dataset, ~46k+ listings x
# 768 dims). So shards are saved as float16 -- half the size, retrieval is
# unaffected in practice, loaders convert back to float32 -- split into files
# of at most this many rows: 60,000 x 768 x 2 bytes = 92 MB per file.
SHARD_ROWS_PER_PART = 60_000
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
}


def comp_image_url(url: str) -> str:
    """Scraped image_urls are small listing-grid thumbnails. Ask the same
    media service for a larger version (same idea as test_extraction.upsize)."""
    url = re.sub(r"([?&])w=\d+", r"\g<1>w=1024", url)
    return re.sub(r"([?&])h=\d+", r"\g<1>h=768", url)


def fetch_comp_image(listing: dict) -> Path | None:
    path = IMAGE_CACHE_DIR / f"{listing['listing_id']}.jpg"
    if path.exists() and path.stat().st_size > 0:
        return path

    original = listing["image_urls"][0]
    last_error = None
    for url in (comp_image_url(original), original):
        try:
            response = requests.get(url, headers=HEADERS, timeout=30)
            response.raise_for_status()
            Image.open(io.BytesIO(response.content)).verify()
            path.write_bytes(response.content)
            time.sleep(REQUEST_DELAY_S)
            return path
        except Exception as e:
            last_error = e
            time.sleep(1)
    print(f"  skipping {listing['listing_id']}: {last_error}")
    return None


def _comp_metadata(listing: dict) -> dict:
    return {
        "listing_id": listing["listing_id"],
        "image_id": f"{listing['listing_id']}_0",
        "image_url": listing["image_urls"][0],
        "price": listing["price"],
        "make": listing.get("make"),
        "model": listing.get("model"),
        "model_family": listing.get("model_family"),
        "year": listing.get("year"),
        "category": listing.get("category"),
        "mileage": listing.get("mileage"),
        "title": listing.get("title"),
        "detail_url": listing.get("detail_url"),
    }


def _load_listings() -> list[dict]:
    with CLEAN_PATH.open(encoding="utf-8") as f:
        listings = [json.loads(line) for line in f if line.strip()]
    return [l for l in listings if l.get("image_urls") and (l.get("price") or 0) > 0]


def _clean_path_hash() -> str:
    """sha256 of truckpaper_clean.jsonl's exact bytes. --shard's split is a
    deterministic slice of this file's listing order, so every shard must
    be built from the identical file -- this hash is how --merge catches a
    mismatch instead of silently producing a wrong/incomplete index."""
    return hashlib.sha256(CLEAN_PATH.read_bytes()).hexdigest()


def shard_part_paths(npy_path: Path, parts: int) -> list[Path]:
    """<stem>.npy, then <stem>.part01.npy, <stem>.part02.npy, ..."""
    npy_path = Path(npy_path)
    return [npy_path] + [npy_path.with_name(f"{npy_path.stem}.part{k:02d}.npy") for k in range(1, parts)]


def save_shard_vectors(npy_path: Path, embeddings: np.ndarray) -> int:
    """Write a shard's vectors as float16 files under GitHub's size limit,
    first removing parts left over from an earlier, larger save. Returns the
    part count, which the shard's .json records as "vector_parts"."""
    npy_path = Path(npy_path)
    for stale in [npy_path, *npy_path.parent.glob(f"{npy_path.stem}.part*.npy")]:
        stale.unlink(missing_ok=True)
    embeddings = np.asarray(embeddings, dtype=np.float16)
    parts = max(1, -(-len(embeddings) // SHARD_ROWS_PER_PART))
    for k, path in enumerate(shard_part_paths(npy_path, parts)):
        np.save(path, embeddings[k * SHARD_ROWS_PER_PART:(k + 1) * SHARD_ROWS_PER_PART])
    return parts


def load_shard_vectors(npy_path: Path, parts: int | None = None) -> np.ndarray:
    """float32 vectors of a shard written by save_shard_vectors. parts=None
    means a shard saved before splitting existed: one float32 .npy."""
    return np.concatenate([np.load(p) for p in shard_part_paths(npy_path, parts or 1)]).astype(np.float32)


def build(shard: tuple[int, int] | None = None) -> None:
    """shard: (i, n) to only download+embed the i-th of n interleaved slices
    of the listings, saving partial embeddings + metadata to SHARD_DIR
    instead of building the index (see --merge). None builds the whole
    thing on this one machine, as before."""
    listings = _load_listings()
    if shard is not None:
        shard_index, shard_total = shard
        listings = listings[shard_index::shard_total]
        print(f"shard {shard_index}/{shard_total}: {len(listings)} of the full listing set")
    IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # Downloads are network-latency-bound, not CPU-bound, so a thread pool
    # gets DOWNLOAD_WORKERS requests in flight at once instead of the old
    # one-at-a-time loop -- this was tonight's real bottleneck (thousands of
    # images x ~1s each serially = hours before embedding even started).
    # Order of `items`/`paths` doesn't need to match `listings`' order --
    # embedding/index-building below doesn't care, it just needs the two
    # lists kept in step with each other, which the completed-future loop
    # already guarantees per listing.
    items, paths = [], []
    checked = 0
    with ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as pool:
        future_to_listing = {pool.submit(fetch_comp_image, listing): listing for listing in listings}
        for future in as_completed(future_to_listing):
            listing = future_to_listing[future]
            checked += 1
            path = future.result()
            if path is not None:
                items.append(_comp_metadata(listing))
                paths.append(path)
            if checked % 50 == 0 or checked == len(listings):
                print(f"images: {checked}/{len(listings)} checked, {len(paths)} usable")

    print(f"embedding {len(paths)} comp images with {MODEL_NAME}...")
    chunks = []
    for start in range(0, len(paths), EMBED_CHUNK):
        chunks.append(embed_images([str(p) for p in paths[start:start + EMBED_CHUNK]]))
        print(f"  embedded {min(start + EMBED_CHUNK, len(paths))}/{len(paths)}")
    embeddings = np.concatenate(chunks) if chunks else np.zeros((0, 0), dtype=np.float32)

    if shard is not None:
        shard_index, shard_total = shard
        SHARD_DIR.mkdir(parents=True, exist_ok=True)
        stem = f"shard_{shard_index}_of_{shard_total}"
        vector_parts = save_shard_vectors(SHARD_DIR / f"{stem}.npy", embeddings)
        payload = {"source_hash": _clean_path_hash(), "vector_parts": vector_parts, "vector_dtype": "float16", "items": items}
        (SHARD_DIR / f"{stem}.json").write_text(json.dumps(payload), encoding="utf-8")
        print(f"wrote shard {shard_index}/{shard_total}: {len(items)} vectors ({vector_parts} file(s)) to {SHARD_DIR}")
        print("Once every shard is built, copy all dino_shards/* files onto one machine "
              f"and run: uv run python build_dino_index.py --merge {shard_total}")
        return

    index = build_index(embeddings)
    save_index(index, items)
    print(f"wrote {index.ntotal} vectors (dim {index.d}) to {INDEX_DIR}")


def merge_shards(total: int, allow_missing: bool = False) -> None:
    """Combine `total` shards previously built via --shard i/total (each run
    on its own machine, then copied here) into the final index. By default,
    refuses to merge if any shard is missing or was built from a different
    truckpaper_clean.jsonl than the others -- either would otherwise
    silently produce a wrong or incomplete index with no error.

    allow_missing: proceed with whichever shards are actually present (e.g.
    one machine dropped out and won't finish in time). The resulting index
    just won't include that shard's slice of listings -- smaller than a
    full run, but not corrupted. Mismatched hashes among the shards that
    ARE present still hard-refuses regardless of this flag."""
    all_embeddings, all_items, hashes, missing = [], [], {}, []
    for i in range(total):
        stem = f"shard_{i}_of_{total}"
        emb_path, meta_path = SHARD_DIR / f"{stem}.npy", SHARD_DIR / f"{stem}.json"
        if not emb_path.exists() or not meta_path.exists():
            if allow_missing:
                missing.append(i)
                continue
            raise SystemExit(
                f"missing shard {i}/{total} in {SHARD_DIR} — run "
                f"`uv run python build_dino_index.py --shard {i}/{total}` (on the machine "
                "assigned that shard) and copy its output files here first, or pass "
                "--allow-missing-shards to merge without it"
            )
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
        hashes[i] = payload.get("source_hash")
        all_embeddings.append(load_shard_vectors(emb_path, payload.get("vector_parts")))
        all_items.extend(payload["items"])

    if not all_items:
        raise SystemExit(f"no shards found in {SHARD_DIR} for total={total} — nothing to merge")
    if missing:
        print(f"proceeding without shard(s) {missing} ({len(missing)}/{total} missing) — "
              f"the index will be smaller than a full {total}-shard run, not corrupted")

    distinct = set(hashes.values())
    if len(distinct) > 1:
        lines = "\n".join(f"  shard {i}: {h}" for i, h in hashes.items())
        raise SystemExit(
            "REFUSING TO MERGE — these shards were built from different "
            f"truckpaper_clean.jsonl files (mismatched sha256):\n{lines}\n"
            "Every machine must shard from the identical file. Re-sync "
            "truckpaper_clean.jsonl across machines and rebuild the "
            "mismatched shard(s) before merging."
        )
    if None in distinct:
        print("warning: at least one shard predates source-hash tracking (rebuilt with an "
              "older build_dino_index.py) — cannot verify it matches the others")

    embeddings = np.concatenate(all_embeddings) if all_embeddings else np.zeros((0, 0), dtype=np.float32)
    index = build_index(embeddings)
    save_index(index, all_items)
    print(f"merged {total} shards -> wrote {index.ntotal} vectors (dim {index.d}) to {INDEX_DIR}")


def sanity_check(examples: int, seed: int = 0) -> None:
    """Leave-one-out retrieval over every comp: each comp queries the index
    with itself excluded. Prints a few example neighborhoods plus summary
    stats, compared against a naive dataset-median-price baseline.

    Reports three visual-pricing numbers:
      image only   — no year information used at all (fusion/reranking alone)
      + noisy year — each comp's true year jittered by +/-1yr uniform noise,
                     simulating the VLM's year_estimate field, then run through
                     pricing.visual_base_price's year-aware adjustment
      + true year  — an optimistic oracle using the comp's exact real year,
                     an upper bound on what year-awareness alone can buy
    """
    index, meta = load_index(INDEX_DIR)
    items = meta["items"]
    vectors = index.reconstruct_n(0, index.ntotal)
    all_prices = np.array([item["price"] for item in items], dtype=float)
    shown = set(random.Random(seed).sample(range(len(items)), min(examples, len(items))))
    noise_rng = random.Random(seed)

    top1, visual_errors, noisy_year_errors, true_year_errors, baseline_errors, strengths = [], [], [], [], [], {}
    for row, item in enumerate(items):
        neighbors = [
            n for n in search(vectors[row:row + 1], k=TOP_K + 1)[0]
            if n["listing_id"] != item["listing_id"]
        ][:TOP_K]
        fused = fuse_retrieval([neighbors])
        visual = visual_base_price(fused)
        strengths[visual["strength"]] = strengths.get(visual["strength"], 0) + 1
        if neighbors:
            top1.append(neighbors[0]["similarity"])

        price = item["price"]
        baseline = float(np.median(np.delete(all_prices, row)))
        baseline_errors.append(abs(baseline - price) / price)
        if visual["visual_base_price"] is not None:
            visual_errors.append(abs(visual["visual_base_price"] - price) / price)

            if item.get("year") is not None:
                noisy_year = item["year"] + noise_rng.uniform(-1, 1)
                noisy = visual_base_price(fused, year_estimate=str(round(noisy_year)))
                noisy_year_errors.append(abs(noisy["visual_base_price"] - price) / price)

                oracle = visual_base_price(fused, year_estimate=str(item["year"]))
                true_year_errors.append(abs(oracle["visual_base_price"] - price) / price)

        if row in shown:
            print(f"\n{item['title']} — ${price:,.0f} ({item['category']})")
            for n in neighbors[:5]:
                print(f"   sim {n['similarity']:.3f}  ${n['price']:>9,.0f}  {n['title']} ({n['category']})")
            if visual["visual_base_price"] is not None:
                print(
                    f"   -> visual base ${visual['visual_base_price']:,.0f} · {visual['strength']} · "
                    f"rel IQR {visual['price_spread']['relative_iqr']:.2f} · confidence {visual['visual_confidence']:.2f}"
                )

    p10, p50, p90 = np.percentile(top1, [10, 50, 90])
    visual_errors = np.array(visual_errors)
    noisy_year_errors = np.array(noisy_year_errors)
    true_year_errors = np.array(true_year_errors)
    print("\n=== leave-one-out summary ===")
    print(f"comps: {len(items)} · top-1 similarity p10/p50/p90: {p10:.3f} / {p50:.3f} / {p90:.3f}")
    print(f"neighborhood strength: {strengths}")
    print(f"median abs % error — dataset median: {np.median(baseline_errors):.1%}")
    within = " · ".join(f"within {p}%: {np.mean(visual_errors <= p / 100):.0%}" for p in (10, 20, 30))
    print(f"  visual, image only:      {np.median(visual_errors):.1%} ({within})")
    if len(noisy_year_errors):
        within = " · ".join(f"within {p}%: {np.mean(noisy_year_errors <= p / 100):.0%}" for p in (10, 20, 30))
        print(f"  visual, + noisy year:    {np.median(noisy_year_errors):.1%} ({within})")
        within = " · ".join(f"within {p}%: {np.mean(true_year_errors <= p / 100):.0%}" for p in (10, 20, 30))
        print(f"  visual, + true year:     {np.median(true_year_errors):.1%} ({within}) [oracle upper bound]")


def _parse_shard(value: str) -> tuple[int, int]:
    try:
        i_str, n_str = value.split("/", 1)
        i, n = int(i_str), int(n_str)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected I/N (e.g. 0/3), got {value!r}") from None
    if n < 1 or not (0 <= i < n):
        raise argparse.ArgumentTypeError(f"expected 0 <= I < N, got {value!r}")
    return i, n


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sanity", type=int, metavar="N", help="after building, print N example neighborhoods + summary")
    parser.add_argument("--sanity-only", type=int, metavar="N", help="skip building; sanity-check the existing index")
    parser.add_argument("--shard", type=_parse_shard, metavar="I/N", help="only download+embed the I-th of N slices of the listings; saves partial results instead of building the index (see module docstring)")
    parser.add_argument("--merge", type=int, metavar="N", help="combine N previously-built shards into the final index")
    parser.add_argument("--allow-missing-shards", action="store_true", help="with --merge, proceed using whichever shards are present instead of requiring all N (e.g. a machine dropped out) -- the index is just smaller, not corrupted")
    args = parser.parse_args()

    if args.merge is not None:
        merge_shards(args.merge, allow_missing=args.allow_missing_shards)
    elif args.sanity_only is not None:
        sanity_check(args.sanity_only)
    else:
        build(shard=args.shard)
        if args.sanity is not None:
            sanity_check(args.sanity)
