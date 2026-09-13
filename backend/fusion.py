"""
Phase 3 — multi-view fusion.

Combines a list of per-photo vision extractions (Phase 2b output — an
unlabeled list from a guided capture session, not named waypoints; see
PLAN.md) into a single fused JSON with the same schema, ready for the
pricing formula (Phase 4b).

Rules (per PLAN.md):
  - make/model/year: majority vote across views, or highest-confidence
    single view if votes are split
  - condition: worst (lowest-scoring) condition seen across views
  - visible_damage: union of all damage flags across views, deduped
  - tire_condition: worst score seen, but only among views that actually
    report tires_visible=True — a photo where tires aren't visible
    shouldn't be able to drag this down with an unreliable guess

Also fuses the per-photo DINO retrieval results (fuse_retrieval) — see
that function for the scoring rule.
"""
from collections import Counter

CONDITION_ORDER = ["poor", "fair", "good", "excellent"]  # worst -> best
TIRE_ORDER = ["bald", "worn", "new"]  # worst -> best

# DINO retrieval fusion. Similarities are cosine similarities between
# L2-normalized DINOv2 embeddings (dino_retrieval.py). Calibrated on the
# 375-comp index: random truck pairs have median ~0.68 (25th pct ~0.41),
# non-truck images score <0.15 against every comp.
RETRIEVAL_MIN_SIMILARITY = 0.50  # neighbors below this are too weak to count as comps at all
# similarity**alpha weights each comp's price in the weighted median. Neighbor
# similarities sit in a narrow band (~0.85-0.95), so a sharp exponent is what
# actually separates close lookalikes from the rest: 2.0 -> 64 lowered median
# abs % error 36.0% -> 33.2% on held-out comps (fixed 375: 27.6% -> 25.0%);
# anything in 48-96 performs about the same.
RETRIEVAL_ALPHA = 64.0
RETRIEVAL_RECURRENCE_BONUS = 0.25  # +25% score per extra view that retrieved the same listing
# +15% score for comps that share the single best-matching neighbor's model_family.
# Comps whose top-1 neighbor shares model_family have ~26% leave-one-out error vs.
# ~49% when it doesn't (diagnostic finding) — nudging the ranking toward the
# apparent family is directionally correct, but most of that gap is a data-coverage
# problem (no same-family comp in the index at all for ~13% of queries), not
# something reranking alone can fix, so keep this bonus modest.
RETRIEVAL_FAMILY_MATCH_BONUS = 0.15
MAX_FUSED_COMPS = 30  # visual neighborhood size kept for pricing


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


def _dedupe_damage(all_damage: list[dict]) -> list[dict]:
    """Dedupe by description (case-insensitive). Boxes are per-source-photo
    coordinates (see module docstring) — kept as-is on whichever entry wins
    the dedupe, never merged/reprojected across views."""
    seen = set()
    deduped = []
    for d in all_damage:
        description = (d.get("description") or "").strip()
        key = description.lower()
        if key and key not in seen:
            seen.add(key)
            deduped.append({"description": description, "box": d.get("box")})
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

    tire_views = [e for e in extractions if e.get("tires_visible")]
    tires_visible = len(tire_views) > 0
    tire_conditions = [e.get("tire_condition") for e in tire_views]
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
        "tires_visible": tires_visible,
        "tire_condition": tire_condition,
        "confidence": round(confidence, 3),
        "views_used": len(extractions),
        "per_view_confidence": [round(c, 3) for c in confidences],
    }


def fuse_retrieval(per_view_neighbors: list[list[dict]]) -> list[dict]:
    """
    per_view_neighbors: one Top-K neighbor list per usable query photo
    (dino_retrieval.retrieve_per_view output). Merges them by listing_id so
    a comp that several different views retrieve counts as one comp that's
    strongly supported, rather than as unrelated matches:

        score = sum(similarity_v ** RETRIEVAL_ALPHA for each view v that retrieved it)
                * (1 + RETRIEVAL_RECURRENCE_BONUS * (views_matched - 1))

    Neighbors under RETRIEVAL_MIN_SIMILARITY are dropped first. A modest
    RETRIEVAL_FAMILY_MATCH_BONUS is then applied to comps that share the
    model_family of the single highest-similarity neighbor across all views
    (a proxy for "what truck this looks like"), since same-family comps price
    far more accurately than mismatched ones. Returns up to MAX_FUSED_COMPS
    comps, best score first, each carrying its metadata plus views_matched,
    best_similarity, mean_similarity and score.
    """
    merged: dict[str, dict] = {}
    best_overall = None  # (similarity, neighbor) — used to pick the reference model_family
    for neighbors in per_view_neighbors:
        best_in_view: dict[str, dict] = {}
        for neighbor in neighbors:
            listing_id = neighbor.get("listing_id")
            similarity = float(neighbor.get("similarity", 0.0))
            if listing_id is None or similarity < RETRIEVAL_MIN_SIMILARITY:
                continue
            if listing_id not in best_in_view or similarity > best_in_view[listing_id]["similarity"]:
                best_in_view[listing_id] = neighbor
            if best_overall is None or similarity > best_overall[0]:
                best_overall = (similarity, neighbor)

        for listing_id, neighbor in best_in_view.items():
            entry = merged.setdefault(listing_id, {
                **{k: v for k, v in neighbor.items() if k != "similarity"},
                "view_similarities": [],
            })
            entry["view_similarities"].append(float(neighbor["similarity"]))

    reference_family = best_overall[1].get("model_family") if best_overall else None

    fused = []
    for entry in merged.values():
        similarities = entry.pop("view_similarities")
        views_matched = len(similarities)
        score = sum(s ** RETRIEVAL_ALPHA for s in similarities) * (
            1 + RETRIEVAL_RECURRENCE_BONUS * (views_matched - 1)
        )
        if reference_family is not None and entry.get("model_family") == reference_family:
            score *= 1 + RETRIEVAL_FAMILY_MATCH_BONUS
        fused.append({
            **entry,
            "views_matched": views_matched,
            "best_similarity": round(max(similarities), 4),
            "mean_similarity": round(sum(similarities) / views_matched, 4),
            # Unrounded: with a sharp alpha, similarity**alpha is tiny (0.85**64 ~ 3e-5)
            # and rounding would zero out real comps' pricing weights.
            "score": score,
        })

    fused.sort(key=lambda c: c["score"], reverse=True)
    return fused[:MAX_FUSED_COMPS]
