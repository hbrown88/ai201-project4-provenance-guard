_LABELS = {
    "LIKELY_AI": (
        "Likely AI-Generated — "
        "Our automated analysis suggests this work was probably produced with an AI writing tool. "
        "This determination is based on stylistic patterns and is not guaranteed to be accurate. "
        "If you are the creator and believe this label is wrong, you can submit an appeal — "
        "we review all appeals within 48 hours."
    ),
    "LIKELY_HUMAN": (
        "Likely Human-Created — "
        "Our automated analysis suggests this work was probably written by a person. "
        "Automated detection is imperfect and this label may not be correct in all cases."
    ),
    "UNCERTAIN": (
        "Origin Uncertain — "
        "Our system was not able to determine with confidence whether this work is human-authored, "
        "AI-generated, or a combination of both. This may reflect a collaborative creative process, "
        "a distinctive personal style, or a content type our tools handle less accurately. "
        "If you are the creator, you can add context or appeal this classification."
    ),
}


def get_label_text(label: str) -> str:
    """Returns the exact transparency label display text from planning.md Section 3."""
    return _LABELS.get(label, _LABELS["UNCERTAIN"])
