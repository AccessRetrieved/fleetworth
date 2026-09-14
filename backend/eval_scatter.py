"""Dump (actual price, predicted price) pairs for a scatter plot, reusing
eval_price_ranges.py's honest leave-one-out evaluation (leakage-excluded
DINO neighbors + leave-one-out lookup buckets) rather than re-deriving it.

Usage:
    uv run python eval_scatter.py --n 1500
"""
import argparse
import json

from eval_price_ranges import bucket_members, neighbor_lists, price_rows, sample_rows
from dino_retrieval import load_index


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=1500)
    parser.add_argument("--out", default="scatter_data.json")
    args = parser.parse_args()

    index, meta = load_index()
    items = meta["items"]
    _, test_rows = sample_rows(len(items), dev=0, test=args.n)

    neighbors = neighbor_lists(index, items, test_rows, leaky=False)
    members = bucket_members()
    records = price_rows(items, test_rows, neighbors, members, leaky=False,
                          extraction_template={"condition": "good", "tire_condition": "worn",
                                                "visible_damage": [], "tires_visible": True})

    out = {}
    for scenario, recs in records.items():
        rows = [{"actual": r["price"], "predicted": r["display_point"],
                 "confidence": r["confidence"], "source": r["source"]} for r in recs]
        out[scenario] = rows
        errs = sorted(abs(r["predicted"] - r["actual"]) / r["actual"] for r in rows)
        median_err = errs[len(errs) // 2]
        print(f"{scenario}: n={len(rows)} median abs % error = {median_err:.1%}")

    with open(args.out, "w") as f:
        json.dump(out, f)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
