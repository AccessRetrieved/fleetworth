"""
Phase 4b — hand-tuned pricing formula.

Combines a base_price_lookup() result with a fused vision-extraction JSON
to produce a price range + confidence score. Pure arithmetic against the
scraped comps data — no AI-guessed price, per PLAN.md's core
explainability requirement.

    price = p_base * (0.5 + 0.3*condition_score + 0.15*tire_score) * (1 - 0.05*damage_count)

Per PLAN.md, the headline output is the range + confidence, not a single
point price — the point estimate above is an internal step used only to
derive the range, and is surfaced solely as a breakdown debug field.
"""
from pricing import base_price_lookup

CONDITION_MAP = {"excellent": 1.0, "good": 0.8, "fair": 0.55, "poor": 0.3}
TIRE_MAP = {"new": 1.0, "worn": 0.6, "bald": 0.2}
BASE_MATCH_CONFIDENCE = {"high": 1.0, "medium": 0.7, "low": 0.4}

MIN_RANGE_PCT = 0.10  # tightest possible range, at full confidence
MAX_RANGE_PCT = 0.45  # widest range, at zero confidence
MIN_MULTIPLIER = 0.15  # floor so a heavily-damaged truck doesn't go to $0/negative


def _range_pct_for_confidence(confidence: float) -> float:
    """Range width scales continuously with confidence — no low/high
    step function. confidence=1.0 -> MIN_RANGE_PCT, confidence=0.0 ->
    MAX_RANGE_PCT, linear in between."""
    confidence = max(0.0, min(1.0, confidence))
    
    
    return MIN_RANGE_PCT + (1 - confidence) * (MAX_RANGE_PCT - MIN_RANGE_PCT)


def compute_price(extraction: dict) -> dict:
    """
    extraction: fused vision-extraction dict (Phase 3 output), expects
    make, model, year_estimate, condition, tire_condition, visible_damage,
    confidence.

    Returns the shape used by the /predict API (Phase 5) — price_range +
    confidence are the headline, not a single point price:
    {
      price_range: [low, high], confidence: float, notes: [str],
      breakdown: {base_price, make, model, year_estimate, condition,
                   damage, tire_condition, views_used, base_price_match,
                   base_price_sample_size, multiplier_applied,
                   internal_point_estimate}
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

    # Internal point estimate — used only to derive the range below, never
    # surfaced as the headline result (see module docstring).
    point_estimate = round(p_base * multiplier, 2)

    # Overall confidence folds in both how sure the vision extraction was
    # AND how well-supported the base-price match is (Phase 4d) — a
    # confident make/model read against a single-comp bucket shouldn't
    # report as confidently as one backed by a dozen real listings.
    base_confidence_score = BASE_MATCH_CONFIDENCE.get(base["confidence"], 0.4)
    overall_confidence = min(vlm_confidence, base_confidence_score)

    range_pct = _range_pct_for_confidence(overall_confidence)
    low_pct, high_pct = range_pct, range_pct

    notes = []

    price_range = [
        round(point_estimate * (1 - low_pct), 2),
        round(point_estimate * (1 + high_pct), 2),
    ]

    return {
        "price_range": price_range,
        "confidence": round(overall_confidence, 3),
        "notes": notes,
        "breakdown": {
            "base_price": p_base,
            "base_price_match": base["match_level"],
            "base_price_sample_size": base["sample_size"],
            "make": make,
            "model": model,
            "year_estimate": year_estimate,
            "condition": condition,
            "damage": [d.get("description", str(d)) if isinstance(d, dict) else d for d in visible_damage],
            "tire_condition": tire_condition,
            "views_used": extraction.get("views_used"),
            "multiplier_applied": round(multiplier, 4),
            "internal_point_estimate": point_estimate,
        },
    }
