"""
Benchmark for the displayed price range (compute_price's price_range) on the
honest, duplicate-cleaned leave-one-out comp set.

Each sampled comp is priced as if it were a user's truck: its own photo
embedding queries the DINO index for TOP_K neighbors with every
benchmark_leakage.py exclusion applied (the comp itself, the same photo, any
near-identical copy, any title+price relist), so a listing's own price can't
leak back. The make/model/year lookup is made leave-one-out the same way (the
comp and its title+price twins are removed from their base_prices bucket for
that query).

The simulated VLM extraction uses the comp's real make/model, its year
jittered by +/-1 (the noise model of build_dino_index.py --sanity-only), and
the condition/tires given by --condition (scraped comps have no condition
labels). The DISPLAYED summary scores compute_price's output exactly as
returned; the rule tables score ranges divided by any condition multiplier
the pricing code reports (breakdown.multiplier_applied, 1 when absent).

Identity scenarios:
  confident  VLM confidence 0.9 -> lookup-centered point, unless the lookup
             can't find the model family
  uncertain  VLM confidence 0.4 -> visual-centered point whenever DINO is strong

Width is (high - low) / point estimate, so 80% means roughly +/-40%.

Two kinds of comparison:
  fixed       each rule as-is (e.g. raw weighted Q10-Q90)
  calibrated  each rule's width scaled by one factor k, the smallest k whose
              pooled dev coverage reaches the target; then scored on the
              disjoint test sample. At matched coverage, the narrower rule is
              the one whose width actually tracks uncertainty.

Usage:
    uv run python eval_price_ranges.py
    uv run python eval_price_ranges.py --leaky   # only the comp itself excluded; robustness check, never for tuning
"""
import argparse
import json
import random
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path

import numpy as np

import pricing
from benchmark_leakage import honest_neighbors
from dino_retrieval import INDEX_DIR, TOP_K, l2_normalize, load_index, search
from fusion import fuse_retrieval
from pricing import visual_base_price
from pricing_formula import _range_pct_for_confidence, compute_price

CLEAN_PATH = Path(__file__).resolve().parent.parent / "data" / "truckpaper_clean.jsonl"
SCENARIOS = {"confident": 0.9, "uncertain": 0.4}
TYPICAL_MULTIPLIER = 0.83  # old formula at condition "good", tires "worn": shows the displayed-space bias
QUANTILE_PAIRS = [(5, 95), (10, 90), (15, 85), (20, 80)]
TARGETS = (0.60, 0.70, 0.80)
TARGET_COVERAGE = 0.80
K_GRID = [round(0.5 + 0.05 * i, 2) for i in range(111)]  # 0.5 .. 6.0
MIN_EFFECTIVE_COMPS = 3.0
SHIPPED_RULE = "Q05–Q95 abs∪point ∨ ±10%, guard"  # what pricing_formula._comparable_range implements


def sample_rows(n_items: int, dev: int, test: int) -> tuple[list[int], list[int]]:
    dev_rows = random.Random(0).sample(range(n_items), dev)  # the same rows as the earlier quick check
    taken = set(dev_rows)
    test_rows = random.Random(1).sample([r for r in range(n_items) if r not in taken], test)
    return dev_rows, test_rows


def neighbor_lists(index, items: list[dict], rows: list[int], leaky: bool) -> list[list[dict]]:
    """TOP_K neighbors per query comp. Honest by default: every
    benchmark_leakage.py exclusion (same listing, same photo, near-identical
    copy, title+price relist) is removed, searching deeper when many are.
    leaky excludes only the comp itself."""
    depth = TOP_K + 40
    queries = np.ascontiguousarray(l2_normalize(np.stack([index.vectors[r] for r in rows])))
    similarities, neighbor_rows = index.search(queries, depth)
    lists = []
    for k, row in enumerate(rows):
        item = items[row]
        candidates = [{**items[r], "similarity": float(s)} for s, r in zip(similarities[k], neighbor_rows[k]) if r >= 0]
        if leaky:
            lists.append([c for c in candidates if c["listing_id"] != item["listing_id"]][:TOP_K])
            continue
        kept, _ = honest_neighbors(item, candidates, TOP_K)
        deeper = depth
        while len(kept) < TOP_K and deeper < index.ntotal:
            deeper *= 4
            kept, _ = honest_neighbors(item, search(queries[k:k + 1], k=deeper)[0], TOP_K)
        lists.append(kept)
    return lists


def bucket_members() -> dict[tuple, list[tuple]]:
    members = defaultdict(list)
    with CLEAN_PATH.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                l = json.loads(line)
                members[(l["make"], l["model_family"], str(l["year"]))].append((l["listing_id"], l.get("title"), l["price"]))
    return members


@contextmanager
def lookup_without(item: dict, members: dict, leaky: bool):
    """Temporarily remove the comp (and, unless leaky, its title+price twins)
    from its base_prices bucket so the lookup can't return its own price."""
    make, family, year = item.get("make"), item.get("model_family"), str(item.get("year"))
    models = pricing._BASE_PRICES.get(make)
    years = models.get(family) if models else None
    saved = years.get(year) if years else None
    if saved is None:
        yield
        return
    twin = (item.get("title"), item["price"])
    keep = [
        price for listing_id, title, price in members[(make, family, year)]
        if listing_id != item["listing_id"] and (leaky or (title, price) != twin)
    ]
    if keep:
        years[year] = {"avg": sum(keep) / len(keep), "price": pricing.aggregate_bucket_prices(keep),
                       "min": min(keep), "max": max(keep), "count": len(keep)}
    else:
        del years[year]
        if not years:
            del models[family]
            if not models:
                del pricing._BASE_PRICES[make]
    try:
        yield
    finally:
        pricing._BASE_PRICES.setdefault(make, models)
        models.setdefault(family, years)
        years[year] = saved


def price_rows(items, rows, neighbors, members, leaky, extraction_template: dict) -> dict[str, list[dict]]:
    year_rng = random.Random(0)
    records = {name: [] for name in SCENARIOS}
    for row, comp_neighbors in zip(rows, neighbors):
        item = items[row]
        year_estimate = str(round(item["year"] + year_rng.uniform(-1, 1))) if item.get("year") else "unknown"
        visual = {"available": True, **visual_base_price(fuse_retrieval([comp_neighbors]), year_estimate=year_estimate)}
        for name, vlm_confidence in SCENARIOS.items():
            extraction = {
                **extraction_template,
                "make": item.get("make") or "unknown",
                "model": item.get("model") or "unknown",
                "year_estimate": year_estimate,
                "confidence": vlm_confidence,
            }
            with lookup_without(item, members, leaky):
                result = compute_price(extraction, visual=visual)
            breakdown = result["breakdown"]
            multiplier = breakdown.get("multiplier_applied", 1.0)
            low, high = result["price_range"]
            records[name].append({
                "price": item["price"],
                "display_point": breakdown["internal_point_estimate"],
                "display_range": (low, high),
                "base_price": breakdown["base_price"],
                "point": breakdown["internal_point_estimate"] / multiplier,
                "shipped": (low / multiplier, high / multiplier),
                "confidence": result["confidence"],
                "visual": visual,
                "source": breakdown["base_price_source"],
                "agreement": breakdown["visual_comps"].get("signal_agreement"),
                "range_method": breakdown.get("range_method"),
            })
    return records


# ---- range rules, base-price space; each takes (record, k) -> (low, high, used_empirical) ----

def confidence_range(rec, scale=1.0):
    pct = min(0.95, scale * _range_pct_for_confidence(rec["confidence"]))
    return rec["point"] * (1 - pct), rec["point"] * (1 + pct)


def fixed_range(rec, pct):
    return rec["point"] * (1 - pct), rec["point"] * (1 + pct)


def usable(rec):
    v = rec["visual"]
    return v.get("strength") == "strong" and bool(v.get("price_quantiles"))


def relative_range(rec, lo, hi, k=1.0):
    """Quantiles as log-ratios to the visual median, scaled by k, re-centered
    on the point estimate."""
    v = rec["visual"]
    median = v["visual_base_price"]
    return (rec["point"] * (v["price_quantiles"][f"q{lo:02d}"] / median) ** k,
            rec["point"] * (v["price_quantiles"][f"q{hi:02d}"] / median) ** k)


def absolute_range(rec, lo, hi, k=1.0):
    """Quantiles in dollars (log-scaled by k about the visual median), stretched
    to include the point estimate."""
    v = rec["visual"]
    median = v["visual_base_price"]
    low = median * (v["price_quantiles"][f"q{lo:02d}"] / median) ** k
    high = median * (v["price_quantiles"][f"q{hi:02d}"] / median) ** k
    return min(low, rec["point"]), max(high, rec["point"])


def wider(a, b):
    return min(a[0], b[0]), max(a[1], b[1])


def empirical_rule(shape):
    """An empirical shape plus the lookup-only fallback (current method)."""
    def rule(rec, k=1.0):
        if not usable(rec):
            return (*confidence_range(rec), False)
        return (*shape(rec, k), True)
    return rule


def guarded(rec, rng):
    """Too few effective comps (one dominant near-match): never narrower than
    the current confidence range."""
    return wider(rng, confidence_range(rec)) if rec["visual"]["effective_comps"] < MIN_EFFECTIVE_COMPS else rng


def rule_set():
    rules = {"current: conf ±10–45%": lambda r, k=1.0: (*confidence_range(r, k), False)}
    for lo, hi in QUANTILE_PAIRS:
        q = f"Q{lo:02d}–Q{hi}"
        rules[f"{q} relative"] = empirical_rule(lambda r, k, lo=lo, hi=hi: relative_range(r, lo, hi, k))
        rules[f"{q} abs∪point"] = empirical_rule(lambda r, k, lo=lo, hi=hi: absolute_range(r, lo, hi, k))
        rules[f"{q} abs∪point ∨ ±10%"] = empirical_rule(lambda r, k, lo=lo, hi=hi: wider(absolute_range(r, lo, hi, k), fixed_range(r, 0.10)))
        rules[f"{q} abs∪point ∨ ½conf"] = empirical_rule(lambda r, k, lo=lo, hi=hi: wider(absolute_range(r, lo, hi, k), confidence_range(r, 0.5)))
        rules[f"{q} abs∪point, guard eff<3"] = empirical_rule(lambda r, k, lo=lo, hi=hi: guarded(r, absolute_range(r, lo, hi, k)))
        rules[f"{q} abs∪point ∨ ±10%, guard"] = empirical_rule(
            lambda r, k, lo=lo, hi=hi: guarded(r, wider(absolute_range(r, lo, hi, k), fixed_range(r, 0.10))))
    return rules


def score(recs, rule, k=1.0, shift=1.0):
    covered, widths, empirical = [], [], 0
    for r in recs:
        low, high, used = rule(r, k)
        covered.append(low * shift <= r["price"] <= high * shift)
        widths.append((high - low) / r["point"])
        empirical += bool(used)
    widths = np.array(widths)
    return {"coverage": float(np.mean(covered)), "median_width": float(np.median(widths)),
            "mean_width": float(np.mean(widths)), "empirical_share": empirical / len(recs), "n": len(recs)}


def calibrate(recs, rule, target) -> float | None:
    for k in K_GRID:
        if score(recs, rule, k)["coverage"] >= target:
            return k
    return None


def row(splits, rule, k):
    pooled = splits["test"]["confident"] + splits["test"]["uncertain"]
    p, c, u = score(pooled, rule, k), score(splits["test"]["confident"], rule, k), score(splits["test"]["uncertain"], rule, k)
    d = score(splits["dev"]["confident"] + splits["dev"]["uncertain"], rule, k)
    return (f"{d['coverage']:6.1%} | {p['coverage']:6.1%} {p['median_width']:7.1%} {p['mean_width']:7.1%} | "
            f"{c['coverage']:6.1%} {c['median_width']:6.1%} | {u['coverage']:6.1%} {u['median_width']:6.1%}")


HEADER = (f"  {'rule':<34}{'k':>5}  {'dev cov':>6} | {'test pooled: cov  med w  mean w':<29}| "
          f"{'confident':<14}| {'uncertain':<14}")


def breakdown_table(title, recs, detail, key):
    groups = defaultdict(list)
    for r in recs:
        groups[key(r)].append(r)
    print(f"\n  {title}")
    print(f"    {'group':<16}{'n':>6}  " + "".join(f"{label[:30]:<32}" for label in detail))
    for group in sorted(groups, key=str):
        cells = "".join(
            f"cov {s['coverage']:5.1%} · width {s['median_width']:5.1%}    "
            for s in (score(groups[group], rule, k) for rule, k in detail.values())
        )
        print(f"    {str(group):<16}{len(groups[group]):>6}  {cells}")


def conf_bucket(r):
    c = r["confidence"]
    return "a <0.4" if c < 0.4 else "b 0.4–0.6" if c < 0.6 else "c 0.6–0.8" if c < 0.8 else "d ≥0.8"


def ess_bucket(r):
    e = r["visual"].get("effective_comps") or 0
    return "a <2" if e < 2 else "b 2–3" if e < 3 else "c 3–5" if e < 5 else "d 5–10" if e < 10 else "e ≥10"


def price_band(r):
    p = r["price"]
    return "a <$20k" if p < 20e3 else "b $20–50k" if p < 50e3 else "c $50–100k" if p < 100e3 else "d ≥$100k"


def displayed_summary(splits, label: str) -> None:
    """compute_price's point estimate and price_range exactly as returned,
    dev + test pooled (nothing here is tuned)."""
    print(f"\nDISPLAYED PRICE — {label}")
    print(f"  {'scenario':<11}{'n':>6}  {'MdAPE':>6}  {'pred/real':>9}  {'under':>6}  {'±10%':>5}  {'±20%':>5}  {'±30%':>5}  "
          f"{'range cov':>9}  {'med width':>9}  {'mean width':>10}")
    for name in SCENARIOS:
        recs = splits["dev"][name] + splits["test"][name]
        ratio = np.array([r["display_point"] / r["price"] for r in recs])
        ape = np.abs(ratio - 1)
        covered = np.array([r["display_range"][0] <= r["price"] <= r["display_range"][1] for r in recs])
        width = np.array([(r["display_range"][1] - r["display_range"][0]) / r["display_point"] for r in recs])
        print(f"  {name:<11}{len(recs):>6}  {np.median(ape):>6.1%}  {np.median(ratio):>9.3f}  {np.mean(ratio < 1):>6.1%}  "
              f"{np.mean(ape <= .10):>5.1%}  {np.mean(ape <= .20):>5.1%}  {np.mean(ape <= .30):>5.1%}  "
              f"{covered.mean():>9.1%}  {np.median(width):>9.1%}  {width.mean():>10.1%}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dev", type=int, default=2000)
    parser.add_argument("--test", type=int, default=5000)
    parser.add_argument("--leaky", action="store_true")
    parser.add_argument("--condition", default="good/worn", help="simulated VLM condition/tire_condition, e.g. excellent/new")
    parser.add_argument("--summary-only", action="store_true", help="only the DISPLAYED summary")
    parser.add_argument("--pick", default=SHIPPED_RULE, help="rule to detail (default: the one compute_price implements)")
    parser.add_argument("--pick-k", type=float, default=1.0, help="width scale for --pick (default 1 = raw quantiles)")
    args = parser.parse_args()

    index, meta = load_index(INDEX_DIR)
    items = meta["items"]
    members = bucket_members()
    dev_rows, test_rows = sample_rows(len(items), args.dev, args.test)
    condition, tire_condition = args.condition.split("/")
    template = {"condition": condition, "tire_condition": tire_condition, "visible_damage": []}
    splits = {
        split: price_rows(items, rows, neighbor_lists(index, items, rows, args.leaky), members, args.leaky, template)
        for split, rows in (("dev", dev_rows), ("test", test_rows))
    }
    print(f"{len(items)} comps · dev {args.dev} / test {args.test} · "
          f"{'LEAKY (self-only exclusion)' if args.leaky else 'duplicate-cleaned'} · top-{TOP_K} single photo")
    displayed_summary(splits, f"condition {condition}, tires {tire_condition}, no damage")
    if args.summary_only:
        return
    for name in SCENARIOS:
        errors = {split: np.median([abs(r["point"] - r["price"]) / r["price"] for r in splits[split][name]]) for split in splits}
        sources = defaultdict(int)
        for r in splits["test"][name]:
            sources[r["source"]] += 1
        print(f"[{name}] median point error dev {errors['dev']:.1%} · test {errors['test']:.1%} · test sources {dict(sources)}")

    rules = rule_set()
    shipped = lambda r, k=1.0: (*r["shipped"], r["range_method"] == "comparable_distribution")

    print("\nFIXED (k=1, raw quantiles)")
    print(HEADER)
    for name, rule in {**rules, "shipped: compute_price": shipped}.items():
        print(f"  {name:<34}{1.0:>5.2f}  {row(splits, rule, 1.0)}")

    pooled_dev = splits["dev"]["confident"] + splits["dev"]["uncertain"]
    ks = {}
    for target in TARGETS:
        print(f"\nCALIBRATED to {target:.0%} pooled dev coverage")
        print(HEADER)
        for name, rule in rules.items():
            k = ks[(name, target)] = calibrate(pooled_dev, rule, target)
            print(f"  {name:<34}" + (f"{k:>5.2f}  {row(splits, rule, k)}" if k is not None else "    —  never reaches target"))

    eligible = [n for n in rules if not n.startswith("current") and ks[(n, TARGET_COVERAGE)] is not None]
    narrowest = min(eligible, key=lambda n: score(pooled_dev, rules[n], ks[(n, TARGET_COVERAGE)])["median_width"])
    print(f"\nnarrowest at {TARGET_COVERAGE:.0%} dev coverage: {narrowest} (k={ks[(narrowest, TARGET_COVERAGE)]})")
    pick, pick_k = args.pick, args.pick_k
    current_k = ks[("current: conf ±10–45%", TARGET_COVERAGE)]
    print(f"detailing: {pick} (k={pick_k})")

    detail = {
        "current (HEAD)": (rules["current: conf ±10–45%"], 1.0),
        f"current × {current_k}": (rules["current: conf ±10–45%"], current_k),
        f"{pick} × {pick_k}": (rules[pick], pick_k),
        "shipped": (shipped, 1.0),
    }
    for name in SCENARIOS:
        recs = splits["test"][name]
        print(f"\n=== test · {name} ===")
        print("  condition good/worn (×0.83), displayed space: " + " · ".join(
            f"{label} cov {score(recs, rule, k, TYPICAL_MULTIPLIER)['coverage']:.1%}" for label, (rule, k) in detail.items()))
        breakdown_table("by confidence bucket", recs, detail, conf_bucket)
        breakdown_table("by effective comps", recs, detail, ess_bucket)
        breakdown_table("by true price band", recs, detail, price_band)
        breakdown_table("by lookup/visual agreement", recs, detail, lambda r: r["agreement"])


if __name__ == "__main__":
    main()
