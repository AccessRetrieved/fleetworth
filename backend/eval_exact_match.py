"""
Benchmark for the exact / near-exact image match path (exact_match.py), kept
separate from the ordinary visual-pricing benchmarks.

Photo queries, for a random sample of comps with a cached photo:
  exact photo             the comp's own photo file, embedded fresh -- a user
                          re-uploading a listing photo
  resized + recompressed  the same photo at 70% size, JPEG quality 60
  honest leave-one-out    the comp's index vector with every leakage neighbor
                          removed (benchmark_leakage.py)
Each is priced "before" (normal fusion + weighted median only) and "after"
(exact_match.select_visual on top), image only, no year, with production code.

Normal benchmark on index vectors (no embedding, larger sample):
  honest       leakage removed -- before and after must be identical, since the
               shortcut can't fire once near-identical copies are gone
  self-only    only the comp itself removed, like a production query for a
               truck whose duplicate listings are indexed -- how many queries
               the shortcut changes and what it does to their error

Usage:
    uv run python eval_exact_match.py
    uv run python eval_exact_match.py --samples 100 --normal-samples 1000
"""
import argparse
import io
import random
import time
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image

from benchmark_leakage import honest_neighbors
from dino_retrieval import INDEX_DIR, TOP_K, embed_images, l2_normalize, load_index, load_model, search, search_near_exact
from exact_match import EXACT_MATCH_SIMILARITY, cluster_is_truncated, select_visual
from fusion import fuse_retrieval
from pricing import visual_base_price

IMAGE_DIR = Path(__file__).resolve().parent.parent / "data" / "comp_images"
SWEEP = (0.99, 0.995, 0.999)
SEARCH_DEPTH = TOP_K + 60


def recompressed(path: Path) -> bytes:
    img = Image.open(path).convert("RGB")
    img = img.resize((max(1, int(img.width * 0.7)), max(1, int(img.height * 0.7))), Image.Resampling.BILINEAR)
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=60)
    return buffer.getvalue()


def price_views(per_view: list[list[dict]], exact_views: list[list[dict]]) -> tuple[dict, dict]:
    normal = visual_base_price(fuse_retrieval(per_view), views_queried=len(per_view))
    return normal, select_visual(normal, exact_views, views_queried=len(per_view))


def error(price, true: float):
    return None if price is None else abs(price - true) / true


def record(item: dict, view: list[dict], exact_view: list[dict], normal: dict, after: dict) -> dict:
    true = float(item["price"])
    exact = after.get("exact_match") or {}
    return {
        "top1": view[0]["similarity"] if view else 0.0,
        "regime": after["match_regime"],
        "distinct": exact.get("distinct_listings", 0),
        "contains_self": any(n["listing_id"] == item["listing_id"] and n["similarity"] >= EXACT_MATCH_SIMILARITY for n in exact_view),
        "before": error(normal["visual_base_price"], true),
        "after": error(after["visual_base_price"], true),
        "identical": normal["visual_base_price"] == after["visual_base_price"],
    }


def fmt(errors) -> str:
    e = np.array([x for x in errors if x is not None])
    if not len(e):
        return "n/a"
    return (f"median {np.median(e):5.1%} · ±0.5% {np.mean(e < 0.005):4.0%} · ±5% {np.mean(e <= 0.05):4.0%} · "
            f"±10% {np.mean(e <= 0.10):4.0%} · ±20% {np.mean(e <= 0.20):4.0%} · ±30% {np.mean(e <= 0.30):4.0%} · "
            f"worst {e.max():.0%}  (n={len(e)})")


def honest_view(query, item: dict, ntotal: int) -> tuple[list[dict], Counter]:
    depth = SEARCH_DEPTH
    while True:
        view, reasons = honest_neighbors(item, search(query, k=depth)[0], TOP_K)
        if len(view) >= TOP_K or depth >= ntotal:
            return view, reasons
        depth *= 4


def report_photo_variant(name: str, records: list[dict]) -> None:
    n = len(records)
    detected = [r for r in records if r["regime"] == "near_exact"]
    print(f"\n== {name} (n={n}) ==")
    print(f"  near-exact path fired (>= {EXACT_MATCH_SIMILARITY}): {len(detected)}/{n} ({len(detected) / n:.0%}) · "
          f"cluster contains the query's own listing: {sum(r['contains_self'] for r in detected)}/{len(detected)} · "
          f"queries with multiple near-exact listings: {sum(r['distinct'] > 1 for r in detected)}")
    print("  top-1 similarity at other thresholds: " + " · ".join(f">= {t}: {np.mean([r['top1'] >= t for r in records]):.0%}" for t in SWEEP))
    print(f"  before (normal weighted median): {fmt(r['before'] for r in records)}")
    print(f"  after  (exact-match path):       {fmt(r['after'] for r in records)}")
    if detected:
        print(f"  detected only, before:           {fmt(r['before'] for r in detected)}")
        print(f"  detected only, after:            {fmt(r['after'] for r in detected)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--samples", type=int, default=300, help="photo queries (embedded fresh)")
    parser.add_argument("--normal-samples", type=int, default=3000, help="index-vector queries for the normal benchmark")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    index, meta = load_index(INDEX_DIR)
    items = meta["items"]
    with_photo = [r for r, item in enumerate(items) if (IMAGE_DIR / f"{item['listing_id']}.jpg").exists()]
    rows = random.Random(args.seed).sample(with_photo, min(args.samples, len(with_photo)))
    print(f"index: {index.ntotal} comps · {len(with_photo)} with cached photos · threshold {EXACT_MATCH_SIMILARITY} · top-{TOP_K}")

    # ---- photo queries ----
    model = load_model()
    photo = {"exact photo": [], "resized + recompressed": []}
    started = time.monotonic()
    for i, row in enumerate(rows, 1):
        item = items[row]
        path = IMAGE_DIR / f"{item['listing_id']}.jpg"
        for variant, image in (("exact photo", str(path)), ("resized + recompressed", recompressed(path))):
            query = embed_images([image], model=model)
            view = search(query, k=TOP_K)[0]
            exact_view = search_near_exact(query, EXACT_MATCH_SIMILARITY)[0] if cluster_is_truncated(view) else view
            normal, after = price_views([view], [exact_view])
            photo[variant].append(record(item, view, exact_view, normal, after))
        if i % 50 == 0:
            print(f"  photo queries: {i}/{len(rows)} ({time.monotonic() - started:.0f}s)", flush=True)
    for variant, records in photo.items():
        report_photo_variant(variant, records)

    honest, excluded = [], Counter()
    for row in rows:
        item = items[row]
        view, reasons = honest_view(index.reconstruct(row)[None], item, index.ntotal)
        excluded.update(reasons)
        normal, after = price_views([view], [view])
        honest.append(record(item, view, view, normal, after))
    print(f"\n== honest leave-one-out, same comps (n={len(honest)}) ==")
    print(f"  leakage neighbors excluded: {dict(excluded)}")
    print(f"  near-exact path fired: {sum(r['regime'] == 'near_exact' for r in honest)} (must be 0) · "
          f"before == after: {sum(r['identical'] for r in honest)}/{len(honest)}")
    print("  remaining top-1 similarity (would-be false positives if the threshold were lower): "
          + " · ".join(f">= {t}: {np.mean([r['top1'] >= t for r in honest]):.1%}" for t in SWEEP))
    print(f"  price: {fmt(r['after'] for r in honest)}")

    # ---- normal benchmark on index vectors ----
    normal_rows = random.Random(args.seed + 1).sample(range(len(items)), min(args.normal_samples, len(items)))
    queries = np.ascontiguousarray(l2_normalize(np.stack([index.reconstruct(r) for r in normal_rows])))
    similarities, neighbor_rows = index.search(queries, SEARCH_DEPTH)
    honest_before, honest_after, honest_identical = [], [], 0
    switched, unchanged, unchanged_identical = [], 0, 0
    for i, row in enumerate(normal_rows):
        item = items[row]
        true = float(item["price"])
        candidates = [{**items[r], "similarity": float(s)} for s, r in zip(similarities[i], neighbor_rows[i]) if r >= 0]

        view, _ = honest_neighbors(item, candidates, TOP_K)
        if len(view) < TOP_K:
            view, _ = honest_view(queries[i:i + 1], item, index.ntotal)
        before, after = price_views([view], [view])
        honest_before.append(error(before["visual_base_price"], true))
        honest_after.append(error(after["visual_base_price"], true))
        honest_identical += after["match_regime"] == "visual_neighbors" and before["visual_base_price"] == after["visual_base_price"]

        self_only = [c for c in candidates if c["listing_id"] != item["listing_id"]][:TOP_K]
        exact_view = self_only
        if cluster_is_truncated(self_only):
            exact_view = [n for n in search_near_exact(queries[i:i + 1], EXACT_MATCH_SIMILARITY)[0] if n["listing_id"] != item["listing_id"]]
        before, after = price_views([self_only], [exact_view])
        if after["match_regime"] == "near_exact":
            switched.append((error(before["visual_base_price"], true), error(after["visual_base_price"], true),
                             after["exact_match"]["distinct_listings"]))
        else:
            unchanged += 1
            unchanged_identical += before["visual_base_price"] == after["visual_base_price"]

    n = len(normal_rows)
    print(f"\n== normal benchmark, honest leave-one-out on index vectors (n={n}) ==")
    print(f"  before == after (shortcut never fires): {honest_identical}/{n}")
    print(f"  before: {fmt(honest_before)}")
    print(f"  after:  {fmt(honest_after)}")
    print(f"\n== normal benchmark, self-only exclusion (production-like, duplicates stay indexed) (n={n}) ==")
    print(f"  shortcut fired: {len(switched)}/{n} ({len(switched) / n:.1%}) · "
          f"with multiple near-exact listings: {sum(d > 1 for _, _, d in switched)} · "
          f"unchanged queries identical before/after: {unchanged_identical}/{unchanged}")
    if switched:
        print(f"  fired, before: {fmt(b for b, _, _ in switched)}")
        print(f"  fired, after:  {fmt(a for _, a, _ in switched)}")


if __name__ == "__main__":
    main()
