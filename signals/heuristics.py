import math
import re
from collections import Counter

_HEDGE_PHRASES = [
    "it is important to", "it's important to",
    "it is worth noting", "it's worth noting",
    "it is essential to", "it's essential to",
    "it is crucial to", "it's crucial to",
    "it is vital to", "it's vital to",
    "it should be noted", "it is worth mentioning",
    "in conclusion", "in summary", "to summarize",
    "furthermore", "moreover", "additionally",
    "this highlights", "this demonstrates", "this underscores",
    "key takeaways", "key strategies", "key considerations",
    "in today's", "moving forward", "going forward",
    "at the end of the day", "needless to say",
    "stakeholders", "paradigm shift", "transformative",
    "unlock your potential", "responsible deployment",
    "various sectors", "it's important that",
]

_PUNCT = set('.,;:!?()[]{}—–-…"\'`')


def sentence_burstiness(text: str) -> float:
    """Low variance in sentence lengths (uniform) → 1.0 (AI-like). High variance → 0.0."""
    sentences = [s.strip() for s in re.split(r'[.!?]+', text) if s.strip()]
    lengths = [len(s.split()) for s in sentences]
    if len(lengths) < 2:
        return 0.5
    mean = sum(lengths) / len(lengths)
    if mean == 0:
        return 0.5
    std = math.sqrt(sum((l - mean) ** 2 for l in lengths) / len(lengths))
    cv = std / mean  # coefficient of variation; 0 = perfectly uniform = AI
    # cv=0 → 1.0 (AI), cv≥1.0 → 0.0 (human)
    return max(0.0, 1.0 - cv)


def lexical_diversity(text: str) -> float:
    """TTR in mid-high range → 1.0 (AI-like). Very low or very high → 0.0 (human-like)."""
    tokens = re.findall(r'\b\w+\b', text.lower())
    if len(tokens) < 10:
        return 0.5
    ttr = len(set(tokens)) / len(tokens)
    # AI text clusters 0.5–0.75 TTR; very low = repetitive human; very high = literary human
    if ttr < 0.40:
        return 0.0
    if ttr <= 0.75:
        return (ttr - 0.40) / 0.35    # 0→1 as ttr goes 0.40→0.75
    return max(0.0, 1.0 - (ttr - 0.75) / 0.25)   # fade out above 0.75


def hedge_phrase_density(text: str) -> float:
    """High density of AI hedge/transition phrases per 100 words → 1.0."""
    word_count = max(len(text.split()), 1)
    text_lower = text.lower()
    count = sum(1 for phrase in _HEDGE_PHRASES if phrase in text_lower)
    density_per_100 = count / (word_count / 100)
    # ≥3 matches per 100 words → score 1.0
    return min(1.0, density_per_100 / 3.0)


def punctuation_entropy(text: str) -> float:
    """Low entropy (orderly punctuation, few types) → 1.0 (AI-like). High entropy → 0.0."""
    puncts = [c for c in text if c in _PUNCT]
    if len(puncts) < 5:
        return 0.5
    total = len(puncts)
    probs = [n / total for n in Counter(puncts).values()]
    entropy = -sum(p * math.log2(p) for p in probs if p > 0)
    # entropy 0 = single type (AI) → 1.0; entropy ≥ 3 = many types (human) → 0.0
    return max(0.0, 1.0 - (entropy / 3.0))


def heuristic_classify(text: str) -> tuple[float, dict]:
    """
    Returns (heuristic_score, sub_scores).
    heuristic_score is in [0, 1]; 1.0 = strongly AI-like.
    sub_scores is a dict of each feature's individual score for debugging.
    """
    sub = {
        "burstiness": sentence_burstiness(text),
        "lexical_diversity": lexical_diversity(text),
        "hedge_density": hedge_phrase_density(text),
        "punct_entropy": punctuation_entropy(text),
    }
    score = sum(sub.values()) / len(sub)
    return round(score, 4), {k: round(v, 4) for k, v in sub.items()}
