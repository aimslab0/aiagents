"""Hybrid retrieval using existing fenced attempts and immutable provider snapshots."""
import json
from django.conf import settings
from agents.consensus import ConsensusClient
from agents.semantic_scholar import SemanticScholarClient
from agents.exceptions import AgentError, ConsensusError
from agents.security import redact
from .academic import normalize_consensus, merge_evidence, rank_evidence, identities
from .models import AgentResponse, Citation
from .metrics import ExecutionTimer, select_attempts
from .execution import retry_call, persistence_guard, touch, LeaseLost
from .planning import save_plan


def retrieve(query, provider, client=None):
    return retry_call(query, provider, lambda: _retrieve(query, provider, client))


def _retrieve(query, provider, client):
    timer = ExecutionTimer()
    plan = query.execution_data.get('research_plan') or save_plan(query)
    client = client or (SemanticScholarClient() if provider == 'semantic_scholar' else ConsensusClient(page_size=settings.CONSENSUS_RESULTS_PER_QUERY))
    papers, requests, raw = [], [], []
    for question in plan['canonical_search_queries'][:settings.MAX_RETRIEVAL_QUERIES]:
        touch(query, provider=provider)
        try:
            result = client.search(question)
            batch = result['papers'] if provider == 'semantic_scholar' else [normalize_consensus(p) for p in result['papers']]
            papers.extend(batch)
            raw.append(result.get('raw_response', {}))
            requests.append({'query': question, 'result_count': len(batch), 'error': None})
        except LeaseLost:
            raise
        except (AgentError, ConsensusError) as error:
            requests.append({'query': question, 'result_count': 0, 'error': error.as_dict()})
        except Exception:
            requests.append({'query': question, 'result_count': 0, 'error': {'code': 'unexpected_error', 'message': 'Retrieval failed safely.'}})
        # Avoid repeating credentials/billing failures or hammering a rate-limited service.
        if requests[-1]['error'] and requests[-1]['error']['code'] in {'authentication', 'configuration', 'billing', 'credits', 'rate_limit'}:
            break
    success = any(r['error'] is None for r in requests)
    data = redact({'provider': provider, 'papers': papers, 'requests': requests, 'raw_response': raw,
                   'error': None if success else requests[0]['error'], 'partial': success and any(r['error'] for r in requests),
                   'research_plan': plan})
    with persistence_guard(query):
        response = AgentResponse.objects.create(research_query=query, provider=provider, provider_key=provider,
            model_name='academic-graph-search' if provider == 'semantic_scholar' else 'search-v1',
            raw_response=json.dumps(data['raw_response'], ensure_ascii=False), normalized_response=data,
            paper_count=len(papers), **timer.finish(success))
    return response


def prepare_hybrid(query, limits=None):
    max_items = limits['items'] if limits else settings.SYNTHESIS_MAX_EVIDENCE_ITEMS
    abstract_chars = limits['abstract_chars'] if limits else settings.SYNTHESIS_MAX_ABSTRACT_CHARS
    context_chars = limits['context_chars'] if limits else settings.SYNTHESIS_MAX_CONTEXT_CHARS
    plan = query.execution_data.get('research_plan') or save_plan(query)
    latest, active = select_attempts(query)
    papers = [p for key in ('semantic_scholar', 'consensus') if key in active for p in active[key].normalized_response.get('papers', [])]
    if limits:
        # Old Deep runs may contain the legacy Consensus snapshot format. Reuse it without retrieval.
        papers = [normalize_consensus(p) if 'source_provider' not in p else p for p in papers]
    merged = merge_evidence(papers)
    ranked = rank_evidence(merged, plan, settings.SYNTHESIS_USE_RECENCY)
    sources, included = [], []
    with persistence_guard(query):
        for paper in ranked:
            keys = set(paper['identity_keys'])
            def citation_keys(c):
                meta = c.metadata or {}
                if limits and 'identity_keys' not in meta and meta.get('provider') == 'consensus':
                    return identities(normalize_consensus({**meta, 'title': c.title, 'url': c.url}))
                return set(meta.get('identity_keys', []))
            citation = next((c for c in query.citations.all() if keys & citation_keys(c)), None)
            if citation is None:
                citation = Citation.objects.create(research_query=query, title=paper['title'][:500], url=paper['url'],
                    source_name=paper['journal_or_venue'][:200], metadata=paper)
            else:
                old = citation.metadata or {}
                history = old.get('provider_records', [])
                citation.metadata = {**old, **paper,
                    'identity_keys': sorted(set(old.get('identity_keys', [])) | keys),
                    'source_providers': list(dict.fromkeys(old.get('source_providers', []) + paper['source_providers'])),
                    'provider_records': history + [r for r in paper['provider_records'] if r not in history]}
                citation.save(update_fields=['metadata'])
            # Attempts and synthesis own their text snapshots; citation identity accumulates provenance.
            for provider in paper['source_providers']:
                if provider in active:
                    active[provider].citations_used.add(citation)
            prefix = 'M' if len(paper['source_providers']) > 1 else 'S' if paper['source_providers'] == ['semantic_scholar'] else 'C'
            sid = f'{prefix}{citation.pk}'
            paper['internal_source_id'] = sid
            if not paper['abstract_or_snippet'].strip() or len(included) >= max_items:
                continue
            item = {key: paper[key] for key in ('title', 'year', 'doi', 'url', 'citation_count', 'influential_citation_count', 'study_type', 'sample_size', 'source_providers', 'priority')}
            item.update(source_id=sid, title=item['title'][:500], journal=paper['journal_or_venue'][:200],
                        abstract_or_snippet=paper['abstract_or_snippet'][:abstract_chars],
                        authors=paper['authors'][:10], takeaway='', quality_metadata={})
            if limits:
                item['relevance_score'] = paper.get('relevance_score')
                item['quality_metadata'] = {key: record.get('metadata', {}).get(key)
                    for record in paper['provider_records'] if record['source_provider'] == 'consensus'
                    for key in ('is_preprint', 'sjr_best_quartile', 'study_count', 'population_type')}
            included.append(item)
            sources.append({'source_id': sid, 'kind': 'academic', 'citation_id': citation.pk, 'title': item['title'],
                            'url': item['url'], 'doi': item['doi'], 'year': item['year'], 'source_name': item['journal'],
                            'source_providers': paper['source_providers']})
        query.execution_data['hybrid_diagnostics'] = {'generated_queries': plan['generated_query_count'],
            'retrieval_queries': len(plan['canonical_search_queries']), 'duplicates_removed': len(papers) - len(merged),
            'unique_papers': len(merged), 'included_papers': len(included),
            'providers': {key: {'requests': len(latest[key].normalized_response.get('requests', [])), 'results': len(latest[key].normalized_response.get('papers', [])),
                                'failed_requests': sum(bool(r['error']) for r in latest[key].normalized_response.get('requests', []))} for key in ('semantic_scholar', 'consensus') if key in latest}}
        query.execution_data['merged_evidence'] = ranked
        query.save(update_fields=['execution_data'])
    context = {'question': query.question, 'research_plan': plan,
        'planner_insights': [{'model': r.model_name, 'interpretation': r.normalized_response['plan']['problem_interpretation'],
                             'uncertainties': r.normalized_response['plan']['uncertainties'][:3]} for r in active.values() if r.normalized_response.get('plan')],
        'agent_findings': [], 'academic_evidence': included,
        'claim_analysis': {'note': 'Planner gaps are hypotheses; assess contradictions from the retrieved text.'},
        'context_limits': {'truncations_and_omissions': [], 'omitted_paper_count': len(merged) - len(included)},
        'evidence_thresholds': {'warnings': ['Retrieval is incomplete; absence of results is not proof of absence.']}}
    # Bound total context by dropping the weakest whole papers, never cutting source IDs.
    def context_size():
        serialized = json.dumps(context, ensure_ascii=False)
        return len(serialized.encode('utf-8')) if limits else len(serialized)
    while included and context_size() > context_chars - 1000:
        included.pop()
        sources.pop()
    context['context_limits']['omitted_paper_count'] = len(merged) - len(included)
    if limits:
        usable_count = sum(bool(p['abstract_or_snippet'].strip()) for p in ranked)
        context['deep_research'] = True
        context['synthesis_budget'] = limits
        context['context_limits'].update(evidence_items_supplied=len(included),
            evidence_items_omitted_budget=usable_count - len(included),
            evidence_items_without_text=len(merged) - usable_count)
    with persistence_guard(query):
        query.execution_data['hybrid_diagnostics']['included_papers'] = len(included)
        query.save(update_fields=['execution_data'])
    return {'context': redact(context), 'sources': redact(sources), 'usable': bool(included)}
