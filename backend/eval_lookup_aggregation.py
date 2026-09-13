"""
How the make/model/year lookup table aggregates each bucket's listing prices --
mean (the previous rule) vs median, a trimmed mean and two median/mean
hybrids -- measured through the pricing pipeline.

data/base_prices.json stores each (make, model_family, year) bucket's base
price as "price" (median at 5+ listings, else mean: the median_if_n>=5 rule
chosen with this script) next to the plain mean "avg". This script rebuilds
that table from data/truckpaper_clean.jsonl once per rule, putting the rule's
statistic in "price" (avg/min/max/count unchanged), and prices the same
duplicate-free benchmark trucks with each table swapped into pricing: the same
honest DINO neighbors, the same noisy year estimate, the same identity
confidence, and compute_price itself (exact match, empirical range,
confidence, no condition multiplier). Only the bucket statistic differs. The
median_if_n>=5 table is asserted equal to base_prices.json, so that column is
production.

Leave-one-out lookup uses the shared benchmark_leakage.py rules: for each
query truck, every clean listing that is leakage for it (same listing, same
photo URL, near-identical photo embedding, title+price relist) is removed from
whichever bucket holds it, and those buckets are re-aggregated under each rule.

Sample: eval_price_ranges.sample_rows (2000 dev / 5000 test by default), year
noise seeded exactly like eval_price_ranges.price_rows.

Usage:
    uv run python eval_lookup_aggregation.py
    uv run python eval_lookup_aggregation.py --dev 200 --test 500
"""
import argparse
import json
import math
import random
import re
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

import pricing
from benchmark_leakage import honest_neighbors, image_key, leakage_reason
from dino_retrieval import INDEX_DIR, TOP_K, l2_normalize, load_index, search
from eval_price_ranges import SCENARIOS, sample_rows
from exact_match import EXACT_MATCH_SIMILARITY
from fusion import fuse_retrieval
from pricing import _normalize_model_family, base_price_lookup, visual_base_price
from pricing_formula import compute_price

CLEAN_PATH = Path(__file__).resolve().parent.parent / "data" / "truckpaper_clean.jsonl"
SEARCH_DEPTH = TOP_K + 40  # eval_price_ranges.neighbor_lists' first search depth
TEMPLATE = {"condition": "good", "tire_condition": "worn", "visible_damage": []}  # doesn't move the price


def mean(prices):
    return sum(prices) / len(prices)


def median(prices):
    return statistics.median(prices)


def trimmed_mean(prices):
    """Buckets of 5+: drop 10% (at least one listing) from each end. Smaller: mean."""
    n = len(prices)
    if n < 5:
        return mean(prices)
    k = max(1, int(n * 0.10))
    return mean(sorted(prices)[k:n - k])


def median_if_at_least(n_min):
    return lambda prices: median(prices) if len(prices) >= n_min else mean(prices)


RULES = {
    "mean": mean,
    "median": median,
    "trimmed_mean": trimmed_mean,
    "median_if_n>=5": median_if_at_least(5),
    "median_if_n>=10": median_if_at_least(10),
}


def bucket_stats(prices, rule):
    return {"price": round(rule(prices), 2), "avg": round(mean(prices), 2), "min": min(prices), "max": max(prices),
            "count": len(prices)}


def load_members():
    members = defaultdict(list)
    with CLEAN_PATH.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                l = json.loads(line)
                key = (l["make"], l["model_family"], str(l["year"]))
                members[key].append({
                    "listing_id": l["listing_id"], "title": l.get("title"), "price": float(l["price"]),
                    "image_url": (l.get("image_urls") or [None])[0], "key": key,
                })
    return members


def build_table(members, rule):
    table = {}
    for (make, family, year), listings in members.items():
        table.setdefault(make, {}).setdefault(family, {})[year] = bucket_stats([m["price"] for m in listings], rule)
    return table


def patched_table(table, members, removed_by_key, rule):
    """Copy-on-write table with leakage listings removed from their buckets."""
    if not removed_by_key:
        return table
    new = dict(table)
    for (make, family, year), removed in removed_by_key.items():
        if make not in new or family not in new[make] or year not in new[make][family]:
            continue
        remaining = [m["price"] for m in members[(make, family, year)] if m["listing_id"] not in removed]
        families = dict(new[make])
        years = dict(families[family])
        if remaining:
            years[year] = bucket_stats(remaining, rule)
        else:
            del years[year]
        if years:
            families[family] = years
        else:
            del families[family]
        if families:
            new[make] = families
        else:
            del new[make]
    return new


class LeakageIndex:
    """Clean listings indexed by the identities benchmark_leakage.py checks."""

    def __init__(self, members):
        self.by_id, self.by_image, self.by_twin = {}, defaultdict(list), defaultdict(list)
        for listings in members.values():
            for m in listings:
                self.by_id[m["listing_id"]] = m
                if image_key(m["image_url"]):
                    self.by_image[image_key(m["image_url"])].append(m)
                self.by_twin[(m["title"], m["price"])].append(m)

    def removed_by_key(self, query, near_identical: list[dict]) -> tuple[dict, Counter]:
        candidates = {}  # listing_id -> (member, similarity)
        if query["listing_id"] in self.by_id:
            candidates[query["listing_id"]] = (self.by_id[query["listing_id"]], 0.0)
        for m in self.by_image.get(image_key(query.get("image_url")), []):
            candidates.setdefault(m["listing_id"], (m, 0.0))
        if query.get("title"):
            for m in self.by_twin.get((query.get("title"), float(query["price"])), []):
                candidates.setdefault(m["listing_id"], (m, 0.0))
        for n in near_identical:
            if n["listing_id"] in self.by_id:
                candidates[n["listing_id"]] = (self.by_id[n["listing_id"]], n["similarity"])
        removed, reasons = defaultdict(set), Counter()
        for listing_id, (m, similarity) in candidates.items():
            reason = leakage_reason(query, m, similarity)
            if reason:
                removed[m["key"]].add(listing_id)
                reasons[reason] += 1
        return removed, reasons


def lookup_bucket(extraction, lookup):
    """(make, model_family, year) the lookup read, or the fallback level name."""
    make, family = (extraction["make"] or "").strip().upper(), _normalize_model_family(extraction["model"])
    if lookup["match_level"] == "exact":
        return (make, family, re.search(r"(\d{4})$", lookup["notes"]).group(1))
    if lookup["match_level"] == "nearest_year":
        return (make, family, re.search(r"nearest year (\d{4})", lookup["notes"]).group(1))
    return None


def run_split(rows, items, index, members, tables, leakage):
    year_rng = random.Random(0)  # eval_price_ranges.price_rows' year noise
    queries = np.ascontiguousarray(l2_normalize(np.stack([index.reconstruct(r) for r in rows])))
    similarities, neighbor_rows = index.search(queries, SEARCH_DEPTH)
    records = {scenario: {rule: [] for rule in RULES} for scenario in SCENARIOS}
    skipped = {scenario: Counter() for scenario in SCENARIOS}
    reasons_total = Counter()
    for k, row in enumerate(rows):
        item = items[row]
        candidates = [{**items[r], "similarity": float(s)} for s, r in zip(similarities[k], neighbor_rows[k]) if r >= 0]
        # Honest DINO neighbors, exactly as eval_price_ranges.neighbor_lists.
        view, _ = honest_neighbors(item, candidates, TOP_K)
        deeper = SEARCH_DEPTH
        while len(view) < TOP_K and deeper < index.ntotal:
            deeper *= 4
            view, _ = honest_neighbors(item, search(queries[k:k + 1], k=deeper)[0], TOP_K)
        # Every near-identical indexed photo, for lookup leakage.
        near = candidates
        depth = SEARCH_DEPTH
        while near and near[-1]["similarity"] >= EXACT_MATCH_SIMILARITY and depth < index.ntotal:
            depth *= 4
            near = search(queries[k:k + 1], k=depth)[0]
        removed, reasons = leakage.removed_by_key(item, [n for n in near if n["similarity"] >= EXACT_MATCH_SIMILARITY])
        reasons_total.update(reasons)

        year_estimate = str(round(item["year"] + year_rng.uniform(-1, 1))) if item.get("year") else "unknown"
        visual = {"available": True, **visual_base_price(fuse_retrieval([view]), year_estimate=year_estimate)}
        row_results = {scenario: {} for scenario in SCENARIOS}
        for rule_name, table in tables.items():
            pricing._BASE_PRICES = patched_table(table, members, removed, RULES[rule_name])
            for scenario, vlm_confidence in SCENARIOS.items():
                extraction = {**TEMPLATE, "make": item.get("make") or "unknown", "model": item.get("model") or "unknown",
                              "year_estimate": year_estimate, "confidence": vlm_confidence}
                lookup = base_price_lookup(extraction["make"], extraction["model"], year_estimate)
                try:
                    result = compute_price(extraction, visual=visual)
                except Exception as e:  # production failure, reported (not patched) -- see skipped counts
                    row_results[scenario][rule_name] = f"{type(e).__name__} at lookup level {lookup['match_level']}"
                    continue
                breakdown = result["breakdown"]
                row_results[scenario][rule_name] = {
                    "price": float(item["price"]),
                    "point": float(breakdown["internal_point_estimate"]),
                    "range": tuple(result["price_range"]),
                    "source": breakdown["base_price_source"],
                    "match_level": breakdown["base_price_match"],
                    "sample_size": breakdown["base_price_sample_size"],
                    "bucket": lookup_bucket(extraction, lookup),
                }
        # A truck counts only if every rule priced it, so rules compare on identical trucks.
        for scenario, by_rule in row_results.items():
            errors = [v for v in by_rule.values() if isinstance(v, str)]
            if errors:
                skipped[scenario][errors[0]] += 1
                continue
            for rule_name, record in by_rule.items():
                records[scenario][rule_name].append(record)
    return records, reasons_total, skipped


def metrics(recs):
    price = np.array([r["price"] for r in recs])
    point = np.array([r["point"] for r in recs])
    low = np.array([r["range"][0] for r in recs])
    high = np.array([r["range"][1] for r in recs])
    ratio = point / price
    ape = np.abs(ratio - 1)
    width = (high - low) / point
    return {
        "n": len(recs), "MdAPE": np.median(ape), "MAPE": ape.mean(), "median pred/real": np.median(ratio),
        "mean pred/real": ratio.mean(), "underpredicted": np.mean(ratio < 1), "±10%": np.mean(ape <= .10),
        "±20%": np.mean(ape <= .20), "±30%": np.mean(ape <= .30), "range coverage": np.mean((low <= price) & (price <= high)),
        "median width": np.median(width), "mean width": width.mean(),
    }


RATIO_KEYS = {"median pred/real", "mean pred/real"}


def fmt(key, value):
    if key == "n":
        return f"{value:,}"
    return f"{value:.3f}" if key in RATIO_KEYS else f"{value:.1%}"


def print_rule_table(title, by_rule):
    stats = {rule: metrics(recs) for rule, recs in by_rule.items() if recs}
    if not stats:
        return stats
    rules = list(stats)
    print(f"\n{title}")
    print(f"  {'metric':<18}" + "".join(f"{r:>17}" for r in rules))
    for key in stats[rules[0]]:
        print(f"  {key:<18}" + "".join(f"{fmt(key, stats[r][key]):>17}" for r in rules))
    return stats


def size_group(sample_size):
    if sample_size is None:
        return "global"
    return "1" if sample_size == 1 else "2" if sample_size == 2 else "3-5" if sample_size <= 5 else ">5"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dev", type=int, default=2000)
    parser.add_argument("--test", type=int, default=5000)
    args = parser.parse_args()
    started = time.monotonic()

    members = load_members()
    tables = {name: build_table(members, rule) for name, rule in RULES.items()}
    production = pricing._BASE_PRICES
    assert tables["median_if_n>=5"] == production, "rebuilt median_if_n>=5 table must equal data/base_prices.json"
    leakage = LeakageIndex(members)

    index, meta = load_index(INDEX_DIR)
    items = meta["items"]
    dev_rows, test_rows = sample_rows(len(items), args.dev, args.test)
    print(f"{len(items)} comps · {sum(len(v) for v in members.values())} clean listings in {len(members)} buckets · "
          f"dev {len(dev_rows)} / test {len(test_rows)} · production table == rule median_if_n>=5")

    splits = {}
    try:
        for split, rows in (("dev", dev_rows), ("test", test_rows)):
            splits[split], reasons, skipped = run_split(rows, items, index, members, tables, leakage)
            print(f"[{split}] lookup leakage listings removed: {dict(reasons)} ({time.monotonic() - started:.0f}s)")
            print(f"[{split}] trucks excluded because compute_price raised (all rules, per scenario): "
                  f"{ {scenario: dict(counts) for scenario, counts in skipped.items()} }")
    finally:
        pricing._BASE_PRICES = production

    compact = {}
    for split in ("dev", "test"):
        for scenario in SCENARIOS:
            recs = splits[split][scenario]
            stats = print_rule_table(f"=== {split.upper()} · {scenario} identity ===", recs)
            compact[(split, scenario)] = stats
            base = recs["mean"]
            changed = {rule: sum(a["point"] != b["point"] for a, b in zip(base, recs[rule])) for rule in RULES if rule != "mean"}
            sources = Counter(r["source"] for r in base)
            levels = Counter(r["match_level"] for r in base if r["source"] == "make_model_year_lookup")
            print(f"  base price source (mean rule): {dict(sources)}")
            print(f"  lookup match levels: {dict(levels)}")
            print(f"  predictions changed vs mean: {changed}")

    # ---- small-bucket check and bucket diagnostics (test, confident) ----
    test_conf = splits["test"]["confident"]
    lookup_idx = [i for i, r in enumerate(test_conf["mean"]) if r["source"] == "make_model_year_lookup"]
    print("\n=== TEST · confident · lookup-priced trucks by bucket size (sample_size after leave-one-out) ===")
    groups = defaultdict(list)
    for i in lookup_idx:
        r = test_conf["mean"][i]
        level = r["match_level"]
        groups[size_group(r["sample_size"]) if level in ("exact", "nearest_year") else f"pooled:{level}"].append(i)
    order = ["1", "2", "3-5", ">5"] + sorted(g for g in groups if g.startswith("pooled"))
    for group in order:
        idx = groups.get(group, [])
        if not idx:
            continue
        print(f"\n  bucket size {group} · {len(idx)} trucks ({len(idx) / len(test_conf['mean']):.1%} of confident test)")
        for rule in ("mean", "median", "trimmed_mean", "median_if_n>=5"):
            m = metrics([test_conf[rule][i] for i in idx])
            same = sum(test_conf[rule][i]["point"] == test_conf["mean"][i]["point"] for i in idx)
            print(f"    {rule:<16} MdAPE {m['MdAPE']:6.1%} · med pred/real {m['median pred/real']:.3f} · mean pred/real {m['mean pred/real']:.3f} · "
                  f"±10% {m['±10%']:5.1%} · ±20% {m['±20%']:5.1%} · cov {m['range coverage']:5.1%} · identical to mean {same}/{len(idx)}")

    used = [test_conf["mean"][i] for i in lookup_idx if test_conf["mean"][i]["bucket"]]
    bucket_sizes = {}
    for r in used:
        bucket_sizes[r["bucket"]] = r["sample_size"]
    all_lookup = len(lookup_idx)
    size_counts = Counter(size_group(r["sample_size"]) for r in used)
    print("\n=== BUCKET DIAGNOSTICS (test · confident · lookup-priced) ===")
    print(f"  lookup-priced trucks: {all_lookup} of {len(test_conf['mean'])} · via a specific bucket (exact/nearest year): {len(used)}")
    print(f"  distinct lookup buckets used: {len(bucket_sizes)} · median listings per used bucket: {statistics.median(bucket_sizes.values()) if bucket_sizes else 0}")
    print("  share of lookup-priced trucks by bucket size: " + " · ".join(
        f"{g}: {size_counts.get(g, 0) / all_lookup:.1%}" for g in ("1", "2", "3-5", ">5")) +
        f" · pooled fallback levels: {(all_lookup - len(used)) / all_lookup:.1%}")

    ratios_all, ratios_3 = [], []
    rows_3 = []
    for key, listings in members.items():
        prices = [m["price"] for m in listings]
        ratio = mean(prices) / median(prices)
        ratios_all.append(ratio)
        if len(prices) >= 3:
            ratios_3.append(ratio)
            rows_3.append((abs(math.log(ratio)), key, len(prices), mean(prices), median(prices), min(prices), max(prices)))
    for label, values in (("all buckets", ratios_all), ("buckets with >=3 listings", ratios_3)):
        q = np.percentile(values, [5, 25, 50, 75, 95])
        print(f"  mean/median over {label} (n={len(values)}): p5 {q[0]:.3f} · p25 {q[1]:.3f} · p50 {q[2]:.3f} · p75 {q[3]:.3f} · "
              f"p95 {q[4]:.3f} · max {max(values):.2f} · >1.05: {np.mean(np.array(values) > 1.05):.1%} · <0.95: {np.mean(np.array(values) < 0.95):.1%}")
    used_keys = set(bucket_sizes)
    used_ratios = [mean([m["price"] for m in members[k]]) / median([m["price"] for m in members[k]]) for k in used_keys if k in members]
    if used_ratios:
        q = np.percentile(used_ratios, [5, 50, 95])
        print(f"  mean/median over buckets used by test lookups (n={len(used_ratios)}): p5 {q[0]:.3f} · p50 {q[1]:.3f} · p95 {q[2]:.3f} · >1.05: {np.mean(np.array(used_ratios) > 1.05):.1%}")

    print("\n  largest mean-vs-median disagreements (buckets with >=3 listings):")
    print(f"    {'make':<22}{'family':<22}{'year':>5}{'n':>5}{'mean':>12}{'median':>12}{'min':>12}{'max':>12}{'mean/med':>9}")
    for _, (make, family, year), n, mu, md, lo, hi in sorted(rows_3, reverse=True)[:12]:
        print(f"    {make[:21]:<22}{family[:21]:<22}{year:>5}{n:>5}{mu:>12,.0f}{md:>12,.0f}{lo:>12,.0f}{hi:>12,.0f}{mu / md:>9.2f}")

    print("\n=== COMPACT · TEST ===")
    for scenario in SCENARIOS:
        stats = compact[("test", scenario)]
        print(f"\n  {scenario} identity (n={stats['mean']['n']:,})")
        print(f"  {'metric':<18}{'mean':>9}{'median':>9}{'delta':>9}{'trimmed':>9}{'med>=5':>9}{'med>=10':>9}")
        for key in ("MdAPE", "MAPE", "median pred/real", "mean pred/real", "underpredicted", "±10%", "±20%", "±30%",
                    "range coverage", "median width", "mean width"):
            a, b = stats["mean"][key], stats["median"][key]
            delta = f"{b - a:+.3f}" if key in RATIO_KEYS else f"{(b - a) * 100:+.1f}pt"
            print(f"  {key:<18}{fmt(key, a):>9}{fmt(key, b):>9}{delta:>9}{fmt(key, stats['trimmed_mean'][key]):>9}"
                  f"{fmt(key, stats['median_if_n>=5'][key]):>9}{fmt(key, stats['median_if_n>=10'][key]):>9}")
    print(f"\ndone in {time.monotonic() - started:.0f}s")


if __name__ == "__main__":
    main()
