SHORT_TEXT_THRESHOLD = 30  # words; below this, heuristics are unreliable

_LLM_WEIGHT = 0.65
_HEU_WEIGHT = 0.35


def aggregate_score(llm_score: float, heuristic_score: float, word_count: int) -> dict:
    """
    Combines both signals into a single calibrated result.
    Matches thresholds from planning.md Section 2.

    Returns dict with: final_score, label, attribution, confidence_level, short_text_flag.
    """
    short_text_flag = word_count < SHORT_TEXT_THRESHOLD

    if short_text_flag:
        # Heuristics unreliable; blend LLM with neutral 0.5 and cap label at UNCERTAIN
        final_score = round(_LLM_WEIGHT * llm_score + _HEU_WEIGHT * 0.5, 4)
        return {
            "final_score": final_score,
            "label": "UNCERTAIN",
            "attribution": "uncertain",
            "confidence_level": "low",
            "short_text_flag": True,
        }

    final_score = round(
        max(0.0, min(1.0, _LLM_WEIGHT * llm_score + _HEU_WEIGHT * heuristic_score)),
        4,
    )

    # Threshold map from planning.md Section 2
    if final_score >= 0.72:
        label, attribution = "LIKELY_AI", "likely_ai"
    elif final_score <= 0.28:
        label, attribution = "LIKELY_HUMAN", "likely_human"
    else:
        label, attribution = "UNCERTAIN", "uncertain"

    # Confidence level: distance from centre maps to certainty
    distance = abs(final_score - 0.5)
    if distance >= 0.22:
        confidence_level = "high"
    elif distance >= 0.05:
        confidence_level = "medium"
    else:
        confidence_level = "low"

    return {
        "final_score": final_score,
        "label": label,
        "attribution": attribution,
        "confidence_level": confidence_level,
        "short_text_flag": False,
    }
