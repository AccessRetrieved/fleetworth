"""
Phase 3 — multi-view fusion.

Combines a list of per-waypoint vision extractions (Phase 2b output, one
per captured photo: front, driver_side, rear, passenger_side, tires,
interior, ...) into a single fused JSON with the same schema, ready for
the pricing formula (Phase 4b).

Rules (per PLAN.md):
  - make/model/year: majority vote across views, or highest-confidence
    single view if votes are split
  - condition: worst (lowest-scoring) condition seen across views
  - visible_damage: union of all damage flags across views, deduped
  - tire_condition: worst score seen (ideally from the dedicated tires
    waypoint, but any view counts if it reports one)
"""
from collections import Counter

CONDITION_ORDER = ["poor", "fair", "good", "excellent"]  # worst -> best
TIRE_ORDER = ["bald", "worn", "new"]  # worst -> best


def _worst(values: list[str], order: list[str], default: str) -> str:
    known = [v for v in values if v in order]
    if not known:
        return default
    return min(known, key=order.index)


def _majority_or_highest_confidence(extractions: list[dict], key: str) -> str:
    """Majority vote on `key` across extractions; ties broken by picking the
    value from whichever single extraction has the highest confidence."""
    values = [e[key] for e in extractions if e.get(key) and e[key] != "unknown"]
    if not values:
        return "unknown"

    counts = Counter(values)
    top_count = max(counts.values())
    tied = [v for v, c in counts.items() if c == top_count]
    if len(tied) == 1:
        return tied[0]

    # Split vote: fall back to the value from the highest-confidence extraction
    # among those tied.
    candidates = [e for e in extractions if e.get(key) in tied]
    best = max(candidates, key=lambda e: e.get("confidence", 0.0))
    return best[key]


def _dedupe_damage(all_damage: list[str]) -> list[str]:
    seen = set()
    deduped = []
    for d in all_damage:
        key = d.strip().lower()
        if key and key not in seen:
            seen.add(key)
            deduped.append(d.strip())
    return deduped


def fuse_extractions(extractions: list[dict]) -> dict:
    """extractions: list of Phase 2b extraction dicts (one per waypoint
    image). Returns one fused dict with the same schema, plus a
    `views_used` count and a `per_view_confidence` list for transparency."""
    if not extractions:
        raise ValueError("fuse_extractions requires at least one extraction")

    make = _majority_or_highest_confidence(extractions, "make")
    model = _majority_or_highest_confidence(extractions, "model")
    year_estimate = _majority_or_highest_confidence(extractions, "year_estimate")

    conditions = [e.get("condition") for e in extractions]
    condition = _worst(conditions, CONDITION_ORDER, default="fair")

    tire_conditions = [e.get("tire_condition") for e in extractions]
    tire_condition = _worst(tire_conditions, TIRE_ORDER, default="worn")

    all_damage = [d for e in extractions for d in (e.get("visible_damage") or [])]
    visible_damage = _dedupe_damage(all_damage)

    confidences = [float(e.get("confidence", 0.0)) for e in extractions]
    # Overall confidence reflects the make/model identification, so weight
    # towards views that actually agreed with the winning make/model rather
    # than a flat average across every view (an "unknown" view shouldn't
    # drag down confidence as much as a confidently-wrong one would).
    agreeing = [
        float(e.get("confidence", 0.0))
        for e in extractions
        if e.get("make") == make
    ]
    confidence = sum(agreeing) / len(agreeing) if agreeing else (sum(confidences) / len(confidences))

    return {
        "make": make,
        "model": model,
        "year_estimate": year_estimate,
        "trim": _majority_or_highest_confidence(extractions, "trim"),
        "condition": condition,
        "visible_damage": visible_damage,
        "tire_condition": tire_condition,
        "confidence": round(confidence, 3),
        "views_used": len(extractions),
        "per_view_confidence": [round(c, 3) for c in confidences],
    }
