from django.conf import settings
from django.utils import timezone

from agents.consensus_schemas import paper_identifiers
from .scoring import number, score_paper
from .support import words


def select_papers(candidates, question, notices):
    """Deduplicate identities, then greedily rank relevance with a diversity penalty."""
    question_words = words(question)
    relevance = sorted({m["relevance_score"] for _, m in candidates if number(m.get("relevance_score"))})
    ranked = []
    for citation, metadata in candidates:
        priority = score_paper(metadata, relevance, timezone.now().year)
        terms = words((metadata.get("title") or citation.title) + " " + (metadata.get("abstract") or "") + " " + (metadata.get("takeaway") or ""))
        overlap = len(terms & question_words) / max(1, len(question_words))
        richness = sum(bool(metadata.get(key)) for key in ("authors", "year", "journal", "abstract", "doi", "study_type", "sample_size"))
        priority["components"].update(question_overlap=round(10 * overlap, 3), metadata_completeness=richness / 2)
        priority["total"] = round(sum(priority["components"].values()), 3)
        ranked.append((citation, metadata, priority))
    ranked.sort(key=lambda item: (-item[2]["total"], item[0].pk))
    unique, identities = [], set()
    for item in ranked:
        ids = paper_identifiers(item[1])
        if ids & identities:
            notices.append({"source_id": f"C{item[0].pk}", "reason": "Duplicate paper identity omitted."})
            continue
        identities.update(ids)
        unique.append(item)
    selected = []
    remaining = list(unique)
    while remaining and len(selected) < settings.SYNTHESIS_MAX_PAPERS:
        choices = []
        for item in remaining:
            title = words(item[1].get("title") or item[0].title)
            selected_titles = [words(other[1].get("title") or other[0].title) for other in selected]
            similarity = max((len(title & other) / max(1, len(title | other)) for other in selected_titles), default=0)
            penalty = 20 * similarity if similarity >= 0.8 else 0
            choices.append((item[2]["total"] - penalty, -item[0].pk, item, penalty))
        _, _, best, penalty = max(choices, key=lambda item: item[:2])
        best[2]["diversity_penalty"] = round(penalty, 3)
        best[2]["selection_score"] = round(best[2]["total"] - penalty, 3)
        selected.append(best)
        remaining.remove(best)
    for item in remaining:
        notices.append({"source_id": f"C{item[0].pk}", "reason": "Paper omitted by context limit after relevance and diversity ranking."})
    return unique, selected
