"""Deterministic round-robin aggregation; no extra model call."""
import re
from django.conf import settings
from .metrics import select_attempts
from .execution import persistence_guard


def tokens(value):
    return set(re.findall(r'\w+', value.casefold()))


def unique(values, limit=30):
    result = []
    for value in values:
        value = ' '.join(value.split())[:400]
        terms = tokens(value)
        if not terms or any(len(terms & tokens(old)) / max(1, len(terms | tokens(old))) >= .85 for old in result):
            continue
        result.append(value)
        if len(result) >= limit:
            break
    return result


def aggregate(question, plans):
    queries = [p['search_queries'][i] for i in range(5) for p in plans if len(p.get('search_queries', [])) > i]
    def merged(key):
        return unique([v[:250] for p in plans for v in p.get(key, [])], 12)
    filters = [p.get('suggested_filters', {}) for p in plans]
    # Suggestions are recorded; applying speculative filters could suppress evidence.
    return {'original_question': question, 'canonical_search_queries': unique(queries, settings.MAX_RETRIEVAL_QUERIES) or [question[:400]],
            'keywords': merged('keywords'), 'synonyms': merged('synonyms'),
            'research_subquestions': merged('research_questions'),
            'candidate_gap_hypotheses': merged('possible_research_gaps'),
            'uncertainties': merged('uncertainties'),
            'filters': {'suggestions': filters, 'policy': 'Advisory only; configured provider filters apply.'},
            'generated_query_count': len(queries), 'successful_planners': len(plans)}


def save_plan(query):
    active = select_attempts(query)[1]
    plans = [r.normalized_response['plan'] for r in active.values() if r.normalized_response.get('plan')]
    plan = aggregate(query.question, plans)
    with persistence_guard(query):
        query.execution_data['research_plan'] = plan
        query.save(update_fields=['execution_data'])
    return plan
