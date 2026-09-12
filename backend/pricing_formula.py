"""
Phase 4b — hand-tuned pricing formula.

Combines a base_price_lookup() result with a fused vision-extraction JSON
to produce a point estimate + range. Pure arithmetic against the scraped
comps data — no AI-guessed price, per PLAN.md's core explainability
requirement.

    price = p_base * (0.5 + 0.3*condition_score + 0.15*tire_score) * (1 - 0.05*damage_count)
"""
from pricing import base_price_lookup

CONDITION_MAP = {"excellent": 1.0, "good": 0.8, "fair": 0.55, "poor": 0.3}
TIRE_MAP = {"new": 1.0, "worn": 0.6, "bald": 0.2}

DEFAULT_RANGE_PCT = 0.15  # +-15% around the point estimate
LOW_CONFIDENCE_RANGE_PCT = 0.30  # widened range when confidence is shaky
MIN_MULTIPLIER = 0.15  # floor so a heavily-damaged truck doesn't go to $0/negative


def compute_price(extraction: dict) -> dict:
    """
    extraction: fused vision-extraction dict (Phase 3 output), expects
    make, model, year_estimate, condition, tire_condition, visible_damage,
    confidence.

    Returns the breakdown shape used by the /predict API (Phase 5):
    {
      price_estimate, price_range: [low, high],
      breakdown: {base_price, make, model, year_estimate, condition,
                   damage, tire_condition, confidence, base_price_match}
    }
    """
    make = extraction.get("make", "unknown")
    model = extraction.get("model", "unknown")
    year_estimate = extraction.get("year_estimate", "unknown")
    condition = extraction.get("condition", "fair")
    tire_condition = extraction.get("tire_condition", "worn")
    visible_damage = extraction.get("visible_damage", []) or []
    vlm_confidence = float(extraction.get("confidence", 0.5))

    base = base_price_lookup(make, model, year_estimate)
    p_base = base["base_price"]

    condition_score = CONDITION_MAP.get(condition, CONDITION_MAP["fair"])
    tire_score = TIRE_MAP.get(tire_condition, TIRE_MAP["worn"])
    damage_count = len(visible_damage)

    multiplier = (0.5 + 0.3 * condition_score + 0.15 * tire_score) * (1 - 0.05 * damage_count)
    multiplier = max(multiplier, MIN_MULTIPLIER)

    price_estimate = round(p_base * multiplier, 2)

    # Widen the range when either the base-price match or the vision
    # extraction itself is shaky (Phase 4d fallback handling).
    low_confidence = base["confidence"] == "low" or vlm_confidence < 0.5
    range_pct = LOW_CONFIDENCE_RANGE_PCT if low_confidence else DEFAULT_RANGE_PCT
    price_range = [
        round(price_estimate * (1 - range_pct), 2),
        round(price_estimate * (1 + range_pct), 2),
    ]

    return {
        "price_estimate": price_estimate,
        "price_range": price_range,
        "breakdown": {
            "base_price": p_base,
            "base_price_match": base["match_level"],
            "base_price_sample_size": base["sample_size"],
            "make": make,
            "model": model,
            "year_estimate": year_estimate,
            "condition": condition,
            "damage": visible_damage,
            "tire_condition": tire_condition,
            "confidence": vlm_confidence,
            "multiplier_applied": round(multiplier, 4),
        },
    }
