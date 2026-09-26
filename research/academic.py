"""Provider-neutral evidence, conservative identity matching and inspectable ranking."""
from copy import deepcopy
import math
import re
from urllib.parse import urlsplit, urlunsplit
from django.utils import timezone
from jsonschema import Draft202012Validator
from agents.consensus_schemas import canonical_doi
from agents.security import safe_source_url
from .support import words

SEMANTIC_PAPER_VALIDATOR = Draft202012Validator({
    'type': 'object', 'required': ['title'], 'properties': {
        **{key: {'type': ['string', 'null']} for key in ('paperId', 'title', 'abstract', 'venue', 'url')},
        'year': {'type': ['integer', 'null']},
        **{key: {'type': ['integer', 'null'], 'minimum': 0} for key in ('citationCount', 'influentialCitationCount')},
        'isOpenAccess': {'type': ['boolean', 'null']},
        'externalIds': {'type': ['object', 'null']}, 'openAccessPdf': {'type': ['object', 'null']},
        'authors': {'type': ['array', 'null'], 'items': {'type': 'object', 'properties': {'name': {'type': ['string', 'null']}}}},
        **{key: {'type': ['array', 'null'], 'items': {'type': 'string'}} for key in ('publicationTypes', 'fieldsOfStudy')},
    }})


def number(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def normalize_semantic(p):
    if not SEMANTIC_PAPER_VALIDATOR.is_valid(p) or not isinstance(p.get('title'), str) or not p['title'].strip():
        raise ValueError('Invalid paper')
    external = p.get('externalIds') or {}
    return {'source_provider': 'semantic_scholar', 'source_id': p.get('paperId') or '',
        'title': p['title'], 'authors': [a['name'] for a in p.get('authors') or [] if isinstance(a, dict) and isinstance(a.get('name'), str)],
        'year': p.get('year') if type(p.get('year')) is int else None,
        'journal_or_venue': p.get('venue') or '', 'abstract_or_snippet': p.get('abstract') or '',
        'url': safe_source_url(p.get('url')), 'doi': canonical_doi(external.get('DOI')),
        'citation_count': number(p.get('citationCount')), 'influential_citation_count': number(p.get('influentialCitationCount')),
        'study_type': ', '.join(p.get('publicationTypes') or []), 'sample_size': None, 'relevance_score': None,
        'open_access': p.get('isOpenAccess') if type(p.get('isOpenAccess')) is bool else None,
        'metadata': {'paperId': p.get('paperId'), 'externalIds': external, 'fieldsOfStudy': p.get('fieldsOfStudy'),
                     'openAccessPdf': p.get('openAccessPdf'), 'publicationTypes': p.get('publicationTypes')}}


def normalize_consensus(p):
    metadata = p.get('api_metadata') or {}
    return {'source_provider': 'consensus', 'source_id': str(metadata.get('id') or ''),
        'title': p['title'], 'authors': p.get('authors') or [], 'year': p.get('year'),
        'journal_or_venue': p.get('journal') or '', 'abstract_or_snippet': p.get('abstract') or p.get('takeaway') or ' '.join((metadata.get('full_text_chunks') or [])[:3]),
        'url': safe_source_url(p.get('url')), 'doi': canonical_doi(p.get('doi')),
        'citation_count': number(p.get('citation_count')), 'influential_citation_count': number(metadata.get('influential_citation_count')),
        'study_type': p.get('study_type') or '', 'sample_size': number(p.get('sample_size')),
        'relevance_score': number(p.get('relevance_score')), 'open_access': None, 'metadata': metadata}


def identities(p):
    ids = set()
    doi = canonical_doi(p.get('doi')) or canonical_doi(p.get('url'))
    if doi:
        ids.add('doi:' + doi)
    meta = p.get('metadata') or {}
    if meta.get('paperId'):
        ids.add('s2:' + str(meta['paperId']))
    for key, value in (meta.get('externalIds') or {}).items():
        if value and key.casefold() != 'doi':
            ids.add('external:' + key.casefold() + ':' + str(value).casefold())
    url = safe_source_url(p.get('url'))
    if url:
        parts = urlsplit(url)
        ids.add('url:' + urlunsplit(('', parts.netloc.casefold(), parts.path.rstrip('/'), parts.query, '')))
    if p.get('year') is not None:
        title = ' '.join(re.findall(r'\w+', p.get('title', '').casefold()))
        if title:
            ids.add(f"title:{title}:{p['year']}")
    return ids


def merge_evidence(papers):
    merged = []
    for original in papers:
        paper = deepcopy(original)
        keys = identities(paper)
        matches = []
        for prefixes in (('doi:',), ('s2:', 'external:'), ('url:',), ('title:',)):
            matches = [p for p in merged if any(k.startswith(prefixes) for k in keys & set(p['identity_keys']))
                       and not (p.get('doi') and paper.get('doi') and p['doi'] != paper['doi'])]
            if matches:
                break
        if not matches:
            paper.update(source_providers=[paper['source_provider']],
                         provider_records=[deepcopy(original)], identity_keys=sorted(keys))
            merged.append(paper)
            continue
        target = matches[0]
        for other in [paper, *matches[1:]]:
            if target.get('doi') and other.get('doi') and target['doi'] != other['doi']:
                continue
            target['identity_keys'] = sorted(set(target['identity_keys']) | identities(other) | set(other.get('identity_keys', [])))
            target['provider_records'].extend(other.get('provider_records', [deepcopy(other)]))
            for provider in other.get('source_providers', [other['source_provider']]):
                if provider not in target['source_providers']:
                    target['source_providers'].append(provider)
            for key, value in other.items():
                if target.get(key) in (None, '', []):
                    target[key] = value
            if other.get('source_provider') == 'consensus' and other.get('relevance_score') is not None:
                target['relevance_score'] = other['relevance_score']
            if other in merged:
                merged.remove(other)
    return merged


def rank_evidence(papers, plan, recency=False):
    question = words(plan['original_question'])
    subquestions = [words(q) for q in plan.get('research_subquestions', [])]
    current_year = timezone.now().year
    ranked = []
    for original in papers:
        p = deepcopy(original)
        terms = words(p['title'] + ' ' + p['abstract_or_snippet'])
        lexical = len(question & terms) / max(1, len(question))
        sub = max([len(q & terms) / max(1, len(q)) for q in subquestions] or [0])
        relevance = max(lexical, sub * .9)
        consensus = p.get('relevance_score')
        # Scores outside the documented normalized range are not guessed/rescaled.
        consensus = consensus if consensus is not None and 0 <= consensus <= 1 else 0
        relevance = max(relevance, consensus)
        count = p.get('citation_count') or 0
        influence = p.get('influential_citation_count') or 0
        age = current_year - p['year'] if type(p.get('year')) is int else None
        recent = age is not None and 0 <= age <= 3
        study = p.get('study_type', '').casefold()
        study_bonus = 1 if any(s in study for s in ('systematic review', 'meta-analysis', 'randomized')) else 0
        secondary = min(math.log1p(count), 8) / 8 + min(math.log1p(influence), 5) / 5 + study_bonus + (1 if recent and recency else 0)
        role = 'foundational' if relevance >= .5 and age is not None and age >= 8 and (count >= 100 or influence >= 10) else 'recent' if recent and relevance >= .4 else 'highly_relevant' if relevance >= .6 else 'supporting'
        p['priority'] = {'score': round(100 * relevance + secondary, 3), 'relevance': round(relevance, 4),
                         'secondary': round(secondary, 3), 'role': role,
                         'reason': 'Relevance first; study metadata, citation influence and optional recency are secondary, not truth scores.'}
        ranked.append(p)
    # Diversity only breaks ties within a relevance tier; citations cannot lift an irrelevant paper.
    ranked.sort(key=lambda p: (-round(p['priority']['relevance'], 1), -p['priority']['score'], p['title']))
    ordered, venues = [], set()
    while ranked:
        tier = round(ranked[0]['priority']['relevance'], 1)
        candidates = [p for p in ranked if round(p['priority']['relevance'], 1) == tier]
        pick = next((p for p in candidates if p['journal_or_venue'] not in venues), candidates[0])
        ordered.append(pick)
        venues.add(pick['journal_or_venue'])
        ranked.remove(pick)
    return ordered
