"""
Phase 4b — hand-tuned pricing formula.

Combines a base price from the scraped comps data with a fused
vision-extraction JSON to produce a price range + confidence score. Pure
arithmetic against real listings — no AI-guessed price, per PLAN.md's core
explainability requirement.

    price = p_base * (0.5 + 0.3*condition_score + 0.15*tire_score) * (1 - 0.05*damage_count)

p_base comes from one of two parallel signals:
  - base_price_lookup(): the (make, model, year) bucket for the truck the
    VLM identified — the default source
  - visual_base_price(): DINO-retrieved visually similar comps
    (pipeline.retrieve_visual_comps) — used instead when the identity is
    uncertain and the visual neighborhood is strong
When both are available they cross-check each other: agreement lifts
confidence, strong disagreement cuts it (which also widens the range).

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

# VLM <-> DINO signal combination (PLAN.md 4b/4d)
IDENTITY_CONFIDENCE_FLOOR = 0.5  # below this VLM make/model/year confidence, the identity is "uncertain"
UNCERTAIN_MATCH_LEVELS = {"make_only", "global_fallback"}  # lookup couldn't find the model family at all
VISUAL_ONLY_CONFIDENCE_CAP = 0.6  # pricing off visual neighbors without a trusted identity never reports above this
AGREEMENT_RATIO = 1.25  # lookup and visual base prices within this factor corroborate each other
DISAGREEMENT_RATIO = 1.6  # ...and beyond this factor they strongly disagree
DISAGREEMENT_CONFIDENCE_PENALTY = 0.6


def _range_pct_for_confidence(confidence: float) -> float:
    """Range width scales continuously with confidence — no low/high
    step function. confidence=1.0 -> MIN_RANGE_PCT, confidence=0.0 ->
    MAX_RANGE_PCT, linear in between."""
    confidence = max(0.0, min(1.0, confidence))
    return MIN_RANGE_PCT + (1 - confidence) * (MAX_RANGE_PCT - MIN_RANGE_PCT)


def compute_price(extraction: dict, visual: dict | None = None) -> dict:
    """
    extraction: fused vision-extraction dict (Phase 3 output), expects
    make, model, year_estimate, condition, tire_condition, visible_damage,
    confidence.
    visual: pipeline.retrieve_visual_comps() output ({available, strength,
    visual_base_price, visual_confidence, ...}), or None when DINO retrieval
    wasn't run — pricing then works exactly as the lookup-only formula.

    Returns the shape used by the /predict API (Phase 5) — price_range +
    confidence are the headline, not a single point price:
    {
      price_range: [low, high], confidence: float, notes: [str],
      breakdown: {base_price, base_price_source, base_price_match,
                   base_price_sample_size, lookup_base_price, make, model,
                   year_estimate, condition, damage, tire_condition,
                   views_used, multiplier_applied, internal_point_estimate,
                   visual_comps}
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
    lookup_price = base["base_price"]
    base_confidence_score = BASE_MATCH_CONFIDENCE.get(base["confidence"], 0.4)

    visual = visual or {"available": False, "reason": "visual retrieval not run"}
    visual_price = visual.get("visual_base_price") if visual.get("available") else None
    visual_strong = visual_price is not None and visual.get("strength") == "strong"
    identity_uncertain = (
        vlm_confidence < IDENTITY_CONFIDENCE_FLOOR or base["match_level"] in UNCERTAIN_MATCH_LEVELS
    )

    notes = []
    if identity_uncertain and visual_strong:
        # DINO fallback (PLAN.md 4d): the VLM couldn't pin down which truck
        # this is, but real listings that look like it exist — price from
        # those instead of a make-wide or dataset-wide average.
        p_base = visual_price
        base_price_source = "visual_neighbors"
        overall_confidence = min(float(visual["visual_confidence"]), VISUAL_ONLY_CONFIDENCE_CAP)
        notes.append(
            "The make/model/year couldn't be identified confidently, so the base price "
            "comes from the most visually similar real listings."
        )
    else:
        p_base = lookup_price
        base_price_source = "make_model_year_lookup"
        # Overall confidence folds in both how sure the vision extraction was
        # AND how well-supported the base-price match is (Phase 4d) — a
        # confident make/model read against a single-comp bucket shouldn't
        # report as confidently as one backed by a dozen real listings.
        overall_confidence = min(vlm_confidence, base_confidence_score)

    # Cross-check the two signals. A global-fallback lookup is just the
    # dataset average — there's no identified truck for DINO to disagree with.
    signal_agreement = None
    if visual_strong and base["match_level"] != "global_fallback" and lookup_price > 0:
        ratio = max(visual_price, lookup_price) / min(visual_price, lookup_price)
        if ratio >= DISAGREEMENT_RATIO:
            signal_agreement = "disagree"
            overall_confidence *= DISAGREEMENT_CONFIDENCE_PENALTY
            notes.append(
                f"Visually similar listings (about ${visual_price:,.0f}) and comps for the identified "
                f"{make} {model} (about ${lookup_price:,.0f}) point to different price levels, "
                "so confidence is lower and the range is wider."
            )
        elif ratio <= AGREEMENT_RATIO:
            signal_agreement = "agree"
            if base_price_source == "make_model_year_lookup":
                # Visual comps corroborate a thin bucket, so the base price is
                # better supported than its sample size alone suggests. Still
                # capped by how sure the VLM was about the identity.
                overall_confidence = min(
                    vlm_confidence, max(base_confidence_score, float(visual["visual_confidence"]))
                )
        else:
            signal_agreement = "partial"

    condition_score = CONDITION_MAP.get(condition, CONDITION_MAP["fair"])
    tire_score = TIRE_MAP.get(tire_condition, TIRE_MAP["worn"])
    damage_count = len(visible_damage)

    multiplier = (0.5 + 0.3 * condition_score + 0.15 * tire_score) * (1 - 0.05 * damage_count)
    multiplier = max(multiplier, MIN_MULTIPLIER)

    # Internal point estimate — used only to derive the range below, never
    # surfaced as the headline result (see module docstring).
    point_estimate = round(p_base * multiplier, 2)

    range_pct = _range_pct_for_confidence(overall_confidence)
    price_range = [
        round(point_estimate * (1 - range_pct), 2),
        round(point_estimate * (1 + range_pct), 2),
    ]

    return {
        "price_range": price_range,
        "confidence": round(overall_confidence, 3),
        "notes": notes,
        "breakdown": {
            "base_price": p_base,
            "base_price_source": base_price_source,
            "base_price_match": base["match_level"],
            "base_price_sample_size": base["sample_size"],
            "lookup_base_price": lookup_price,
            "make": make,
            "model": model,
            "year_estimate": year_estimate,
            "condition": condition,
            "damage": [d.get("description", str(d)) if isinstance(d, dict) else d for d in visible_damage],
            "tire_condition": tire_condition,
            "views_used": extraction.get("views_used"),
            "multiplier_applied": round(multiplier, 4),
            "internal_point_estimate": point_estimate,
            "visual_comps": {**visual, "signal_agreement": signal_agreement},
        },
    }
