"""Predicted vs. actual price scatter plots for the DINOv2 pricing signal.

Reuses eval_price_ranges.py's honest leave-one-out evaluation (leakage
excluded per benchmark_leakage.py -- a comp's own listing_id, photo,
near-identical copy, and title+price relists are all removed from its own
DINO neighbors and lookup bucket, so no answer leaks into its own
prediction) rather than re-deriving the evaluation logic.

Produces two PNGs:
  price_accuracy_uncertain.png -- DINO-only pricing (simulated low VLM
    identity confidence, no lookup blending). Isolates what the visual
    retrieval model alone is worth.
  price_accuracy_confident.png -- blended pricing (simulated confident
    identity: lookup table + DINO signal combined per the shipped rule).

Usage:
    uv run python plot_price_accuracy.py --n 1500
    uv run python plot_price_accuracy.py --n 1500 --out-dir ../data/plots
"""
import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from eval_price_ranges import bucket_members, neighbor_lists, price_rows, sample_rows
from dino_retrieval import load_index

SCENARIO_LABELS = {
    "uncertain": "DINO-only (uncertain identity)",
    "confident": "Blended (confident identity)",
}
SCENARIO_NOTES = {
    "uncertain": "Simulates an unrecognized truck: price comes entirely from the\n"
                 "DINOv2 visual-neighbor signal, no lookup-table blending.",
    "confident": "Simulates a confidently-identified truck: price blends the\n"
                 "make/model/year lookup table with the DINO signal when they agree.",
}


def evaluate(n: int) -> dict[str, list[dict]]:
    index, meta = load_index()
    items = meta["items"]
    _, test_rows = sample_rows(len(items), dev=0, test=n)
    neighbors = neighbor_lists(index, items, test_rows, leaky=False)
    members = bucket_members()
    records = price_rows(
        items, test_rows, neighbors, members, leaky=False,
        extraction_template={"condition": "good", "tire_condition": "worn",
                              "visible_damage": [], "tires_visible": True},
    )
    return records


def plot_scenario(scenario: str, records: list[dict], out_path: Path) -> None:
    actual = np.array([r["price"] for r in records])
    predicted = np.array([r["display_point"] for r in records])
    keep = (actual > 0) & (predicted > 0)
    actual, predicted = actual[keep], predicted[keep]

    abs_pct_err = np.abs(predicted - actual) / actual
    median_err = np.median(abs_pct_err)
    within10 = np.mean(abs_pct_err <= 0.10)
    within20 = np.mean(abs_pct_err <= 0.20)
    within30 = np.mean(abs_pct_err <= 0.30)

    good = abs_pct_err <= 0.20

    fig, ax = plt.subplots(figsize=(8, 7.5), dpi=150)
    ax.scatter(actual[good], predicted[good], s=14, alpha=0.45, color="#1d6f5c",
               edgecolors="none", label="within ±20% of actual")
    ax.scatter(actual[~good], predicted[~good], s=14, alpha=0.55, color="#a6421f",
               edgecolors="none", label="outside ±20%")

    lo = min(actual.min(), predicted.min()) * 0.9
    hi = max(actual.max(), predicted.max()) * 1.1
    ax.plot([lo, hi], [lo, hi], linestyle="--", color="#4d5c57", linewidth=1.3,
             alpha=0.7, label="y = x (perfect prediction)")

    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlim(lo, hi)
    ax.set_ylim(lo, hi)
    ax.set_xlabel("Actual listed price")
    ax.set_ylabel("Predicted price")
    ax.set_title(f"DINOv2 pricing accuracy — {SCENARIO_LABELS[scenario]}\n"
                 f"n={len(actual)} · median abs error {median_err:.1%} · "
                 f"within 10%/20%/30%: {within10:.0%}/{within20:.0%}/{within30:.0%}",
                 fontsize=10.5)
    ax.legend(loc="upper left", fontsize=9, framealpha=0.9)
    ax.grid(True, which="both", alpha=0.25)
    fig.text(0.5, -0.02, SCENARIO_NOTES[scenario], ha="center", fontsize=8.5, color="#4d5c57")
    fig.tight_layout()
    fig.savefig(out_path, bbox_inches="tight")
    plt.close(fig)
    print(f"{scenario}: n={len(actual)} median abs % error = {median_err:.1%} -> wrote {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--n", type=int, default=1500, help="number of comps to sample (default 1500)")
    parser.add_argument("--out-dir", default=".", help="directory to write PNGs to (default: current dir)")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    records = evaluate(args.n)
    for scenario in ("uncertain", "confident"):
        plot_scenario(scenario, records[scenario], out_dir / f"price_accuracy_{scenario}.png")


if __name__ == "__main__":
    main()
