import json
from copy import deepcopy
from datetime import timedelta
from unittest.mock import Mock, patch

import requests
from django.conf import settings
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from agents.openrouter import OpenRouterClient
from agents.planner import LIST_FIELDS
from agents.semantic_scholar import SemanticScholarClient, SemanticScholarError
from agents.test_consensus import search_response
from agents.test_synthesizer import judge_response, judge_result
from chats.services import save_question
from .academic import normalize_semantic, normalize_consensus, merge_evidence, rank_evidence
from .configuration import select_configuration, allowed_judges
from .planning import aggregate
from .services import run_research, retry_research
from .recovery import recover_query
from .models import Citation


def planner_result():
    return {'problem_interpretation': 'Investigate sleep and memory', **{k: [] for k in LIST_FIELDS},
        'research_questions': ['Does sleep improve memory?'], 'search_queries': ['sleep memory', 'sleep memory randomized trial', 'sleep memory longitudinal'],
        'suggested_filters': {'year_from': None, 'year_to': None, 'fields': [], 'study_types': []}}


def planner_response():
    return Mock(status_code=200, text=json.dumps({'choices': [{'message': {'content': json.dumps(planner_result())}, 'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 100, 'completion_tokens': 100, 'cost': .001}}))


def semantic_paper(**overrides):
    return {'paperId': 'abc', 'title': 'Sleep and memory', 'abstract': 'Sleep improves memory in the studied population.',
        'year': 2025, 'authors': [{'name': 'A Researcher'}], 'venue': 'Sleep Research',
        'url': 'https://www.semanticscholar.org/paper/abc', 'externalIds': {'DOI': '10.1234/sleep'},
        'citationCount': 50, 'influentialCitationCount': 3, 'publicationTypes': ['JournalArticle'], **overrides}


def semantic_response(papers=None):
    return Mock(status_code=200, text=json.dumps({'data': [semantic_paper()] if papers is None else papers}))


@override_settings(OPENROUTER_API_KEY='mock', PROVIDER_BUDGETS={})
class PlannerTests(SimpleTestCase):
    def test_each_configured_planner_uses_short_schema_independently(self):
        for model in settings.RESEARCH_PROFILES['balanced'][0]:
            with self.subTest(model=model['id']), patch('requests.post', return_value=planner_response()) as post:
                result = OpenRouterClient(planner=True).research('sleep?', model['id'])
                payload = post.call_args.kwargs['json']
                self.assertEqual(payload['model'], model['id'])
                self.assertEqual(payload['response_format']['json_schema']['name'], 'research_plan')
                self.assertLessEqual(payload['max_tokens'], 1600)
                self.assertEqual(result['plan'], planner_result())
                self.assertEqual(payload['messages'][1]['content'], 'sleep?')

    def test_deduplicates_round_robin_limits_and_keeps_uncertainty(self):
        plans = [planner_result() for _ in range(3)]
        plans[1]['search_queries'][0] = 'MEMORY sleep!'
        plans[2]['search_queries'].append('sleep learning students')
        plans[0]['possible_research_gaps'] = ['Possible gap only']
        result = aggregate('question', plans)
        self.assertEqual(len(result['canonical_search_queries']), 4)
        self.assertEqual(result['candidate_gap_hypotheses'], ['Possible gap only'])
        self.assertEqual(result['generated_query_count'], 10)

    def test_no_planners_falls_back_to_question(self):
        self.assertEqual(aggregate('Original question', [])['canonical_search_queries'], ['Original question'])

    def test_planner_malformed_json_is_rejected(self):
        response = planner_response()
        response.text = json.dumps({'choices': [{'message': {'content': '{broken'}, 'finish_reason': 'stop'}]})
        from agents.exceptions import AgentError
        with patch('requests.post', return_value=response), self.assertRaises(AgentError) as caught:
            OpenRouterClient(planner=True).research('question', settings.OPENROUTER_MODELS[0]['id'])
        self.assertEqual(caught.exception.code, 'malformed_response')

    @override_settings(MAX_RETRIEVAL_QUERIES=2)
    def test_query_budget(self):
        self.assertEqual(len(aggregate('question', [planner_result()])['canonical_search_queries']), 2)


class SemanticTests(SimpleTestCase):
    @override_settings(SEMANTIC_SCHOLAR_API_KEY='')
    def test_success_fields_and_optional_key(self):
        with patch('requests.get', return_value=semantic_response()) as get:
            result = SemanticScholarClient().search('sleep memory')
        p = result['papers'][0]
        self.assertEqual(p['doi'], '10.1234/sleep')
        self.assertEqual(p['influential_citation_count'], 3)
        self.assertIsNone(p['sample_size'])
        self.assertIsNone(p['open_access'])
        self.assertEqual(get.call_args.kwargs['headers'], {})
        self.assertEqual(get.call_args.kwargs['params']['query'], 'sleep memory')

    def test_empty_results(self):
        with patch('requests.get', return_value=semantic_response([])):
            self.assertEqual(SemanticScholarClient().search('x')['papers'], [])

    def test_timeout(self):
        with patch('requests.get', side_effect=requests.Timeout), self.assertRaises(SemanticScholarError) as caught:
            SemanticScholarClient().search('x')
        self.assertEqual(caught.exception.code, 'timeout')

    def test_auth_rate_limit_and_provider_errors(self):
        for status, code in [(401, 'authentication'), (403, 'authentication'), (429, 'rate_limit'), (503, 'api_error')]:
            with self.subTest(status=status), patch('requests.get', return_value=Mock(status_code=status)), self.assertRaises(SemanticScholarError) as caught:
                SemanticScholarClient().search('x')
            self.assertEqual(caught.exception.code, code)

    def test_malformed_metadata(self):
        for paper in ({}, semantic_paper(abstract=123), semantic_paper(externalIds='bad'), semantic_paper(authors='bad')):
            with self.subTest(paper=paper), patch('requests.get', return_value=semantic_response([paper])), self.assertRaises(SemanticScholarError):
                SemanticScholarClient().search('x')

    @override_settings(SEMANTIC_SCHOLAR_API_KEY='secret-s2')
    def test_secret_redaction(self):
        from agents.security import redact
        self.assertEqual(redact('secret-s2'), '[REDACTED]')
        with patch('requests.get', return_value=semantic_response()) as get:
            SemanticScholarClient().search('x')
        self.assertEqual(get.call_args.kwargs['headers'], {'x-api-key': 'secret-s2'})


class EvidenceTests(SimpleTestCase):
    def test_doi_dedup_and_provenance(self):
        first = normalize_semantic(semantic_paper())
        second = {**first, 'source_provider': 'consensus', 'url': 'https://doi.org/10.1234/sleep', 'metadata': {'sample_size': 80}}
        result = merge_evidence([first, second])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['source_providers'], ['semantic_scholar', 'consensus'])
        self.assertEqual(len(result[0]['provider_records']), 2)

    def test_title_year_fallback_and_conflicting_doi(self):
        first = normalize_semantic(semantic_paper(externalIds={}, paperId=None, url=None))
        second = {**first, 'source_provider': 'consensus', 'title': 'Sleep AND memory!'}
        self.assertEqual(len(merge_evidence([first, second])), 1)
        self.assertEqual(len(merge_evidence([{**first, 'doi': '10.1234/one'}, {**second, 'doi': '10.1234/two'}])), 2)

    def test_external_identity(self):
        first = normalize_semantic(semantic_paper(externalIds={'CorpusId': '123'}))
        second = deepcopy(first)
        second.update(title='Different title', year=2000, url='', metadata={'externalIds': {'CorpusId': '123'}})
        self.assertEqual(len(merge_evidence([first, second])), 1)

    def test_doi_match_wins_over_title_fallback(self):
        first = normalize_semantic(semantic_paper(title='First title', externalIds={'DOI': '10.1234/first'}))
        second = normalize_semantic(semantic_paper(title='Other title', paperId='other', url='', externalIds={'DOI': '10.1234/second'}))
        third = {**first, 'title': 'Other title', 'source_provider': 'consensus'}
        merged = merge_evidence([first, second, third])
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0]['source_providers'], ['semantic_scholar', 'consensus'])

    def test_consensus_normalization_preserves_missing_values(self):
        from agents.consensus_schemas import normalize_search
        paper = normalize_consensus(normalize_search({'results': [{'title': 'Title'}]}, 'q')['papers'][0])
        self.assertIsNone(paper['sample_size'])
        self.assertIsNone(paper['open_access'])
        self.assertIsNone(paper['influential_citation_count'])

    def test_relevance_beats_popularity(self):
        relevant = normalize_semantic(semantic_paper(citationCount=0))
        popular = normalize_semantic(semantic_paper(title='Quantum entanglement', abstract='Particle interactions', citationCount=100000))
        ranked = rank_evidence([popular, relevant], aggregate('sleep memory', []))
        self.assertEqual(ranked[0]['title'], relevant['title'])

    def test_foundational_requires_age_influence_and_relevance(self):
        papers = [normalize_semantic(semantic_paper(year=2000, citationCount=500)),
                  normalize_semantic(semantic_paper(year=2000, citationCount=0, influentialCitationCount=0)),
                  normalize_semantic(semantic_paper(year=timezone.now().year))]
        ranked = rank_evidence(papers, aggregate('sleep memory', []))
        self.assertEqual({p['priority']['role'] for p in ranked}, {'foundational', 'highly_relevant', 'recent'})


@override_settings(OPENROUTER_API_KEY='mock', CONSENSUS_API_KEY='mock', SEMANTIC_SCHOLAR_API_KEY='',
    OPENROUTER_FREE_TEST_MODE=False, PROVIDER_BUDGETS={}, OPENROUTER_MAX_RETRIES=0, CONSENSUS_MAX_RETRIES=0,
    SEMANTIC_SCHOLAR_MAX_RETRIES=0, SEMANTIC_SCHOLAR_ENABLED=True)
class HybridFlowTests(TestCase):
    def setUp(self):
        self.bad_provider = None
        self.bad_planner = None
        self.invalid_source = False
        self.context = None
        self.post_patch = patch('requests.post', side_effect=self.post)
        self.get_patch = patch('requests.get', side_effect=self.get)
        self.http_post = self.post_patch.start()
        self.http_get = self.get_patch.start()
        self.addCleanup(self.post_patch.stop)
        self.addCleanup(self.get_patch.stop)

    def post(self, url, **kwargs):
        payload = kwargs['json']
        if payload['response_format']['json_schema']['name'] == 'final_synthesis':
            self.context = json.loads(payload['messages'][1]['content'])
            ids = ['M999999'] if self.invalid_source else self.context['allowed_source_ids']
            return judge_response(judge_result(ids))
        if payload['model'] == self.bad_planner:
            raise requests.Timeout
        return planner_response()

    def get(self, url, **kwargs):
        provider = 'semantic_scholar' if 'semanticscholar' in url else 'consensus'
        if provider == self.bad_provider:
            raise requests.Timeout
        return semantic_response() if provider == 'semantic_scholar' else search_response()

    def run_query(self):
        query = save_question('Does sleep improve memory?', selection=select_configuration('balanced'))
        run_research(query)
        return query

    def test_full_pipeline_persistence_bounded_context_and_dashboard(self):
        query = self.run_query()
        self.assertEqual(query.stage, 'COMPLETED')
        self.assertTrue(query.final_response.answer)
        self.assertEqual(query.agent_responses.count(), 5)
        self.assertEqual(self.http_post.call_count, 4)
        self.assertEqual(self.http_get.call_count, 6)
        self.assertEqual(self.context['agent_findings'], [])
        self.assertIn('research_plan', self.context)
        self.assertNotIn('raw_response', json.dumps(self.context))
        self.assertLessEqual(len(self.context['academic_evidence']), settings.SYNTHESIS_MAX_EVIDENCE_ITEMS)
        self.assertTrue(Citation.objects.filter(research_query=query).exists())
        page = self.client.get(reverse('chats:detail', args=[query.chat_id]))
        for label in ('Semantic Scholar', 'Planner 1', 'Plan-and-Solve diagnostics', 'Retry Semantic Scholar'):
            self.assertContains(page, label)

    def test_semantic_failure_consensus_succeeds(self):
        self.bad_provider = 'semantic_scholar'
        query = self.run_query()
        self.assertEqual(query.stage, 'PARTIAL')
        self.assertTrue(query.final_response.answer)
        self.assertTrue(all(p['source_providers'] == ['consensus'] for p in self.context['academic_evidence']))

    def test_consensus_failure_semantic_succeeds(self):
        self.bad_provider = 'consensus'
        query = self.run_query()
        self.assertEqual(query.stage, 'PARTIAL')
        self.assertTrue(query.final_response.answer)
        self.assertTrue(all(p['source_id'].startswith('S') for p in self.context['academic_evidence']))

    def test_one_planner_fails(self):
        self.bad_planner = settings.RESEARCH_PROFILES['balanced'][0][0]['id']
        query = self.run_query()
        self.assertEqual(query.execution_data['research_plan']['successful_planners'], 2)
        self.assertTrue(query.final_response.answer)

    def test_alternative_judge_reuses_evidence(self):
        query = self.run_query()
        self.http_get.reset_mock()
        self.http_post.reset_mock()
        retry_research(query, 'synthesis', settings.ALTERNATIVE_SYNTHESIZER_MODEL)
        self.http_get.assert_not_called()
        self.assertEqual(self.http_post.call_count, 1)
        self.assertEqual(query.synthesis_attempts.count(), 2)
        self.assertEqual(query.final_response.active_attempt.model_name, settings.ALTERNATIVE_SYNTHESIZER_MODEL)

    def test_unknown_source_rejected_preserves_accepted_answer(self):
        query = self.run_query()
        previous = query.final_response.answer
        self.invalid_source = True
        retry_research(query, 'synthesis')
        self.assertEqual(query.final_response.answer, previous)
        self.assertFalse(query.synthesis_attempts.latest('pk').succeeded)

    def test_retry_and_recovery_preserve_plan_and_history(self):
        self.bad_provider = 'semantic_scholar'
        query = self.run_query()
        self.bad_provider = None
        self.http_post.reset_mock()
        retry_research(query, 'semantic_scholar')
        self.http_post.assert_not_called()
        self.assertEqual(query.agent_responses.filter(provider='semantic_scholar').count(), 2)
        query.status = 'processing'
        query.execution_data['synthesis'] = True
        query.lease_expires_at = timezone.now() - timedelta(seconds=1)
        query.save()
        recover_query(query)
        self.assertTrue(query.execution_data['research_plan'])
        self.assertEqual(query.stage, 'COMPLETED')

    def test_free_mode_preserves_legacy_pipeline_and_allowlist(self):
        with override_settings(OPENROUTER_FREE_TEST_MODE=True):
            selection = select_configuration('balanced')
            self.assertTrue(selection['free_test'])
            self.assertNotIn('pipeline', selection)

    def test_real_browser_submission_endpoint_uses_planners(self):
        from .models import ResearchQuery
        response = self.client.post(reverse('chats:submit'), {'question': 'Does sleep improve memory?', 'research_mode': 'balanced'}, follow=True)
        self.assertEqual(response.status_code, 200)
        query = ResearchQuery.objects.get()
        self.assertEqual(query.execution_data['selection']['pipeline'], 'plan_and_solve')
        self.assertTrue(query.final_response.answer)
        self.assertContains(response, 'Semantic Scholar')

    def test_empty_retrieval_does_not_synthesize_planner_speculation(self):
        def empty(url, **kwargs):
            return Mock(status_code=200, text=json.dumps({'data': []} if 'semanticscholar' in url else {'results': []}))
        self.http_get.side_effect = empty
        query = self.run_query()
        self.assertEqual(self.http_post.call_count, 3)
        self.assertFalse(query.final_response.answer)
        self.assertEqual(query.synthesis_attempts.latest('pk').synthesis_data['error']['code'], 'insufficient_evidence')

    def test_all_planners_fail_but_retrieval_can_answer(self):
        original = self.post
        def fail_planners(url, **kwargs):
            if kwargs['json']['response_format']['json_schema']['name'] != 'final_synthesis':
                raise requests.Timeout
            return original(url, **kwargs)
        self.http_post.side_effect = fail_planners
        query = self.run_query()
        self.assertTrue(query.final_response.answer)
        self.assertEqual(query.execution_data['research_plan']['canonical_search_queries'], [query.question])

    def test_partial_retrieval_keeps_papers_and_can_retry(self):
        original = self.get
        def partial(url, **kwargs):
            if 'semanticscholar' in url and kwargs['params']['query'] == 'sleep memory randomized trial':
                raise requests.Timeout
            return original(url, **kwargs)
        self.http_get.side_effect = partial
        query = self.run_query()
        self.assertEqual(query.stage, 'PARTIAL')
        self.assertTrue(query.final_response.answer)
        self.assertTrue(query.agent_responses.get(provider='semantic_scholar').normalized_response['partial'])
        self.http_get.side_effect = self.get
        retry_research(query, 'failed')
        self.assertEqual(query.agent_responses.filter(provider='semantic_scholar').count(), 2)

    def test_cross_provider_shared_id_and_provenance(self):
        consensus = json.loads(search_response().text)['results'][0]
        original = self.get
        def shared(url, **kwargs):
            if 'semanticscholar' in url:
                return semantic_response([semantic_paper(externalIds={'DOI': consensus['doi']})])
            return original(url, **kwargs)
        self.http_get.side_effect = shared
        query = self.run_query()
        self.assertEqual(query.citations.count(), 1)
        self.assertEqual(len(self.context['academic_evidence']), 1)
        self.assertTrue(self.context['allowed_source_ids'][0].startswith('M'))
        self.assertEqual(set(query.citations.get().metadata['source_providers']), {'consensus', 'semantic_scholar'})

    def test_retrieval_disabled_independently(self):
        with override_settings(SEMANTIC_SCHOLAR_ENABLED=False):
            query = self.run_query()
        self.assertTrue(query.final_response.answer)
        self.assertFalse(query.agent_responses.filter(provider='semantic_scholar').exists())

    def test_auth_error_stops_batch(self):
        original = self.get
        def denied(url, **kwargs):
            return Mock(status_code=401) if 'semanticscholar' in url else original(url, **kwargs)
        self.http_get.side_effect = denied
        query = self.run_query()
        self.assertEqual(len(query.agent_responses.get(provider='semantic_scholar').normalized_response['requests']), 1)
        self.assertTrue(query.final_response.answer)
