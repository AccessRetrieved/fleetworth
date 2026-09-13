"""
Phase 4b — pricing formula.

Combines a base price from the scraped comps data with a fused
vision-extraction JSON to produce a price range + confidence score. Pure
arithmetic against real listings — no AI-guessed price, per PLAN.md's core
explainability requirement.

    price = p_base

Condition, tire condition and visible damage are extracted and reported in the
breakdown as visible evidence, but they do not move the price. The earlier
hand-tuned multiplier, (0.5 + 0.3*condition + 0.15*tires) * (1 - 0.05*damage),
was never fitted to prices and topped out at 0.95, so it priced every truck
below its comps even though the base price itself is essentially unbiased
(eval_price_ranges.py). It stays out until condition's effect is fitted
against condition-labeled listing prices.

p_base comes from one of two parallel signals:
  - base_price_lookup(): the (make, model, year) bucket for the truck the
    VLM identified — the default source
  - visual_base_price(): DINO-retrieved visually similar comps
    (pipeline.retrieve_visual_comps) — used instead when the identity is
    uncertain and the visual neighborhood is strong
  - an exact image match (exact_match.py): a submitted photo is (nearly)
    identical to an indexed listing's photo, so that listing's price -- or
    the median of its duplicate listings -- is used ahead of both
When both are available they cross-check each other: agreement lifts
confidence, strong disagreement cuts it (which also widens the range).

Per PLAN.md, the headline output is the range + confidence, not a single
point price — the point estimate above is an internal step used only to
derive the range, and is surfaced solely as a breakdown debug field.

Range and confidence answer different questions. Confidence is how far the
identification/retrieval can be trusted; the range is how much comparable
trucks' prices actually vary. When DINO returned a strong comp neighborhood,
the range comes from those comps' weighted price distribution (the same
weights as the visual median), stretched to include the point estimate, with
a small floor. Without a usable neighborhood — index missing, weak or no
matches — it falls back to the confidence-derived width.
"""
from pricing import base_price_lookup

BASE_MATCH_CONFIDENCE = {"high": 1.0, "medium": 0.7, "low": 0.4}

MIN_RANGE_PCT = 0.10  # tightest possible range, at full confidence
MAX_RANGE_PCT = 0.45  # widest range, at zero confidence

# Comparable-distribution range (see module docstring), chosen with
# eval_price_ranges.py on duplicate-cleaned leave-one-out comps: raw weighted
# Q05-Q95 with the floor and guard below covered ~80% of true prices at a
# ~74% median width, vs. ~50% coverage for the confidence formula. Narrower
# pairs undercover (Q10-Q90 ~72%, Q20-Q80 ~61%) because the point estimate's
# own error adds to the comps' spread.
RANGE_LOW_QUANTILE = "q05"  # keys of visual_base_price's price_quantiles
RANGE_HIGH_QUANTILE = "q95"
RANGE_MIN_HALF_WIDTH = 0.10  # never tighter than +/-10% of the point estimate
# Below this many effective comps one near-match carries nearly all the weight
# and the quantiles collapse onto its price: keep at least the confidence range.
RANGE_MIN_EFFECTIVE_COMPS = 3.0

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


def _comparable_range(point_estimate: float, visual: dict, confidence: float) -> tuple[float, float] | None:
    """Range from the retrieved comps' weighted price quantiles. None when
    the visual signal carries no quantiles."""
    quantiles = visual.get("price_quantiles") or {}
    q_low, q_high = quantiles.get(RANGE_LOW_QUANTILE), quantiles.get(RANGE_HIGH_QUANTILE)
    if not q_low or not q_high:
        return None

    low, high = q_low, q_high
    # A lookup-centered point can sit outside the comps' band; the range
    # always contains the estimate it is built around.
    low = min(low, point_estimate * (1 - RANGE_MIN_HALF_WIDTH))
    high = max(high, point_estimate * (1 + RANGE_MIN_HALF_WIDTH))
    # An exact image match concentrates on one listing by design; its
    # uncertainty floor is RANGE_MIN_HALF_WIDTH above plus its capped confidence.
    if visual.get("match_regime") != "near_exact" and float(visual.get("effective_comps") or 0) < RANGE_MIN_EFFECTIVE_COMPS:
        pct = _range_pct_for_confidence(confidence)
        low, high = min(low, point_estimate * (1 - pct)), max(high, point_estimate * (1 + pct))
    return low, high


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
                   views_used, condition_affects_price, internal_point_estimate,
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
    # Match quality and sample support are separate limits. Twelve listings
    # reach full support; one listing can support at most 50% confidence.
    support = min(1.0, 0.5 + max(0, base["sample_size"] - 1) / 22)
    base_confidence_score = min(base_confidence_score, support)
    lookup_spread = max(0.0, base.get("relative_spread", 0.0))
    base_confidence_score /= 1 + lookup_spread

    visual = visual or {"available": False, "reason": "visual retrieval not run"}
    visual_price = visual.get("visual_base_price") if visual.get("available") else None
    visual_strong = visual_price is not None and visual.get("strength") == "strong"
    identity_uncertain = (
        vlm_confidence < IDENTITY_CONFIDENCE_FLOOR or base["match_level"] in UNCERTAIN_MATCH_LEVELS
    )

    exact_match = visual.get("exact_match") if visual.get("match_regime") == "near_exact" and visual_price is not None else None
    exact_price_wins = exact_match is not None and not exact_match.get("conflict")

    notes = []
    if base["sample_size"] < 3 and not exact_price_wins:
        # About make/model/year bucket support; an exact image match isn't
        # priced from that bucket.
        notes.append("Few matching listings are available, so the estimate has a wider range.")
    if exact_price_wins:
        # A submitted photo is (nearly) identical to a listing already in the
        # comps index: its real price -- or the median of its duplicate
        # listings -- beats any bucket average or lookalike blend.
        p_base = visual_price
        base_price_source = "exact_image_match"
        overall_confidence = float(visual["visual_confidence"])
        copies = exact_match["distinct_listings"] - 1
        notes.append(
            "A submitted photo matches a listing already in our comps data, so the base price comes from that listing"
            + (f" and its {copies} duplicate listing{'s' if copies > 1 else ''}." if copies else ".")
        )
    elif identity_uncertain and visual_strong:
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

    if exact_match is not None and exact_match.get("conflict"):
        notes.append(
            "Different photos match different known listings with different prices, "
            "so the visual price pools them and confidence is lower."
        )

    # Cross-check the two signals. A global-fallback lookup is just the
    # dataset average — there's no identified truck for DINO to disagree with.
    signal_agreement = None
    if visual_strong and base["match_level"] != "global_fallback" and lookup_price > 0:
        ratio = max(visual_price, lookup_price) / min(visual_price, lookup_price)
        if ratio >= DISAGREEMENT_RATIO and exact_price_wins:
            # The matched listing's own price is the evidence; a bucket
            # average disagreeing with it says more about the bucket.
            signal_agreement = "disagree"
        elif ratio >= DISAGREEMENT_RATIO:
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
                    vlm_confidence, (base_confidence_score + float(visual["visual_confidence"])) / 2
                )
        else:
            signal_agreement = "partial"

    # Price dispersion remains adverse evidence even if the medians agree,
    # including neighborhoods classified as weak because of that dispersion.
    if visual_price is not None and base_price_source == "make_model_year_lookup":
        spread = max(0.0, (visual.get("price_spread") or {}).get("relative_iqr", 0.0))
        overall_confidence /= 1 + spread

    # Internal point estimate: the selected base price itself, used only to
    # derive the range below and never surfaced as the headline result.
    # Condition, tires and damage are reported, not priced (module docstring).
    point_estimate = round(p_base, 2)

    comparable = _comparable_range(point_estimate, visual, overall_confidence) if visual_strong else None
    if comparable is not None:
        low, high = comparable
        range_method = "comparable_distribution"
    else:
        range_pct = _range_pct_for_confidence(overall_confidence)
        low, high = point_estimate * (1 - range_pct), point_estimate * (1 + range_pct)
        range_method = "confidence"
    price_range = [round(low, 2), round(high, 2)]

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
            "condition_affects_price": False,
            "internal_point_estimate": point_estimate,
            "range_method": range_method,
            "visual_comps": {**visual, "signal_agreement": signal_agreement},
        },
    }
