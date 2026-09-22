import math

from django.conf import settings


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def score_paper(metadata, relevance_values, current_year):
    """Ordering heuristic only; never a truth score or final-answer decision."""
    weights = settings.SYNTHESIS_SCORE_WEIGHTS
    parts = {}
    relevance = metadata.get("relevance_score")
    if number(relevance) and relevance_values:
        # Rank within this query avoids assuming Consensus scores have a fixed scale.
        parts["relevance"] = weights["relevance"] * (relevance_values.index(relevance) + 1) / len(relevance_values)
    if (metadata.get("study_type") or "").lower() in {"systematic review", "meta-analysis"}:
        parts["review"] = weights["review"]
    quality = metadata.get("api_metadata") or {}
    if quality.get("is_peer_reviewed") is True:
        parts["peer_review"] = weights["peer_review"]
    quartile = quality.get("sjr_best_quartile")
    if number(quartile) and 1 <= quartile <= 4:
        parts["journal_quartile"] = weights["journal_quartile"] * (5 - quartile) / 4
    for field, cap, key in [("citation_count", 1000, "citations"), ("sample_size", 10000, "sample_size")]:
        value = metadata.get(field)
        if number(value) and value >= 0:
            parts[key] = weights[key] * min(math.log1p(value) / math.log1p(cap), 1)
    year = metadata.get("year")
    if settings.SYNTHESIS_USE_RECENCY and number(year) and 0 < year <= current_year:
        parts["recency"] = weights["recency"] * max(0, 1 - (current_year - year) / 10)
    return {"total": round(sum(parts.values()), 3), "components": {key: round(value, 3) for key, value in parts.items()}}
