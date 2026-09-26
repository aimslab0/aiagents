import json
from copy import deepcopy
from unittest.mock import Mock, patch

from django.conf import settings
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from agents.deep_synthesis import DEEP_SCHEMA, DEEP_PROMPT, normalize_deep
from agents.synthesis_schemas import SynthesisError
from agents.test_consensus import search_response
from agents.test_synthesizer import judge_response
from chats.services import save_question
from .academic import normalize_semantic
from .configuration import select_configuration, deep_synthesis_budget
from .models import AgentResponse
from .preparation import prepare_evidence
from .services import run_research, retry_research
from .test_plan_solve import planner_response, semantic_response, semantic_paper


def deep_result(ids=None):
    ids = ids or []
    return {'executive_summary': 'Evidence suggests a context-dependent association.',
        'methodology_groups': [{'methodology': 'Observational', 'summary': 'Design details remain limited.', 'source_ids': ids}],
        'major_findings': [{'finding': 'Sleep and memory association', 'evidence_strength': 'limited',
                            'explanation': 'Population differences limit inference.', 'source_ids': ids}],
        'academic_agreements': ['A cautious association is reported.'],
        'academic_disagreements': [{'issue': 'Methodological differences', 'positions': ['Different measurement approaches'],
                                    'possible_explanations': 'Population and measurement differ.', 'source_ids': ids}],
        'research_gaps': [{'gap': 'Longitudinal evidence uncertain', 'why_it_matters': 'Temporality matters.',
                           'evidence_basis': 'Limited retrieved designs; not proof of absence.', 'source_ids': ids}],
        'methodological_limitations': ['Selection bias remains possible.'],
        'future_research_directions': ['Assess longitudinal designs.'],
        'literature_review': 'Studies suggest an association, with heterogeneous designs. ' + ' '.join(f'[{sid}]' for sid in ids),
        'overall_confidence': 55, 'source_ids': ids}


class DeepSchemaTests(SimpleTestCase):
    sources = [{'source_id': 'M1', 'kind': 'academic'}]

    def test_grouping_disagreements_and_gaps_parse(self):
        data = deep_result(['M1'])
        result = normalize_deep(json.dumps(data), self.sources)
        self.assertEqual(result['final_answer'], data['literature_review'])
        self.assertEqual(result['deep_research'], data)
        self.assertEqual(result['key_findings'][0]['supporting_source_ids'], ['M1'])

    def test_unknown_nested_ids_rejected(self):
        for key in ('methodology_groups', 'major_findings', 'academic_disagreements', 'research_gaps'):
            value = deep_result(['M1'])
            value[key][0]['source_ids'] = ['S999']
            with self.subTest(key=key), self.assertRaises(SynthesisError) as caught:
                normalize_deep(json.dumps(value), self.sources)
            self.assertEqual(caught.exception.code, 'untraceable_source')

    def test_unknown_nested_prose_reference_rejected(self):
        value = deep_result(['M1'])
        value['research_gaps'][0]['evidence_basis'] = 'A claim [C999]'
        with self.assertRaises(SynthesisError):
            normalize_deep(json.dumps(value), self.sources)

    def test_agent_ids_not_accepted_as_academic_evidence(self):
        with self.assertRaises(SynthesisError):
            normalize_deep(json.dumps(deep_result(['A1'])), [{'source_id': 'A1', 'kind': 'agent'}])

    def test_malformed_output_rejected(self):
        with self.assertRaises(SynthesisError):
            normalize_deep('{broken', self.sources)


@override_settings(OPENROUTER_FREE_TEST_MODE=False, OPENROUTER_API_KEY='mock', CONSENSUS_API_KEY='mock',
                   DEEP_DEFAULT_SYNTHESIZER='deepseek', PROVIDER_BUDGETS={}, OPENROUTER_MAX_RETRIES=0,
                   CONSENSUS_MAX_RETRIES=0, SEMANTIC_SCHOLAR_ENABLED=True)
class DeepReviewTests(TestCase):
    def setUp(self):
        self.contexts = []
        def post(url, **kwargs):
            payload = kwargs['json']
            if payload['response_format'].get('json_schema', {}).get('name') == 'final_synthesis' or payload['response_format']['type'] == 'json_object':
                self.assertTrue(payload['messages'][0]['content'].startswith(DEEP_PROMPT))
                if payload['response_format']['type'] == 'json_object':
                    self.assertIn(json.dumps(DEEP_SCHEMA), payload['messages'][0]['content'])
                else:
                    self.assertEqual(payload['response_format']['json_schema']['schema'], DEEP_SCHEMA)
                context = json.loads(payload['messages'][1]['content'])
                self.contexts.append(context)
                return judge_response(deep_result(context['allowed_source_ids']))
            return planner_response()
        consensus_doi = json.loads(search_response().text)['results'][0]['doi']
        def get(url, **kwargs):
            return semantic_response([semantic_paper(externalIds={'DOI': consensus_doi})]) if 'semanticscholar' in url else search_response()
        self.post_patcher, self.get_patcher = patch('requests.post', side_effect=post), patch('requests.get', side_effect=get)
        self.post, self.get = self.post_patcher.start(), self.get_patcher.start()
        self.addCleanup(self.post_patcher.stop)
        self.addCleanup(self.get_patcher.stop)

    def test_combined_evidence_deduplicated_default_and_alternative(self):
        query = save_question('Sleep and memory?', selection=select_configuration('deep'))
        run_research(query)
        self.assertEqual(query.stage, 'COMPLETED')
        self.assertEqual(query.final_response.active_attempt.model_name, 'deepseek/deepseek-r1')
        self.assertEqual(len(self.contexts[0]['academic_evidence']), 1)
        self.assertEqual(set(self.contexts[0]['academic_evidence'][0]['source_providers']), {'consensus', 'semantic_scholar'})
        self.assertTrue(self.contexts[0]['academic_evidence'][0]['source_id'].startswith('M'))
        self.assertNotIn('raw_response', json.dumps(self.contexts[0]))
        self.assertIn('research_plan', self.contexts[0])
        self.get.reset_mock()
        self.post.reset_mock()
        retry_research(query, 'synthesis', model_id=next(o['id'] for o in settings.DEEP_SYNTHESIZER_OPTIONS if o['key'] == 'claude'))
        self.get.assert_not_called()
        self.assertEqual(self.post.call_count, 1)
        self.assertEqual(query.synthesis_attempts.count(), 2)
        self.assertEqual(query.final_response.active_attempt.model_name, 'anthropic/claude-sonnet-5')
        page = self.client.get(reverse('chats:detail', args=[query.chat_id]))
        for text in ('Methodology groups', 'Research gaps', 'Evidence items supplied: 1', 'Input tokens:', 'Output tokens:'):
            self.assertContains(page, text)

    def stored_query(self):
        query = save_question('Sleep memory', selection=select_configuration('deep'))
        papers = [normalize_semantic(semantic_paper(paperId=str(i), title=f'Sleep memory study {i}',
                    year=2000+i, externalIds={}, url=f'https://example.org/p/{i}', abstract='sleep memory ' * 600)) for i in range(25)]
        AgentResponse.objects.create(research_query=query, provider='semantic_scholar', provider_key='semantic_scholar',
            model_name='stored', raw_response='{}', normalized_response={'papers': papers, 'error': None}, succeeded=True)
        return query

    def test_model_specific_evidence_and_abstract_budgets(self):
        query = self.stored_query()
        for option in settings.DEEP_SYNTHESIZER_OPTIONS:
            limits = deep_synthesis_budget(option['id'])
            prepared = prepare_evidence(query, model_id=option['id'])
            papers = prepared['context']['academic_evidence']
            self.assertEqual(len(papers), limits['items'])
            self.assertLessEqual(max(len(p['abstract_or_snippet']) for p in papers), limits['abstract_chars'])
            self.assertEqual(prepared['context']['context_limits']['evidence_items_omitted_budget'], 25-limits['items'])
            self.assertEqual(len(prepared['sources']), len(papers))
        self.get.assert_not_called()
        self.post.assert_not_called()

    def test_small_context_drops_whole_papers_not_json(self):
        query = self.stored_query()
        budgets = deepcopy(settings.DEEP_SYNTHESIS_BUDGETS)
        budgets['deepseek']['context_chars'] = 7000
        with override_settings(DEEP_SYNTHESIS_BUDGETS=budgets):
            prepared = prepare_evidence(query, 'deepseek/deepseek-r1')
        self.assertLess(len(prepared['sources']), 8)
        self.assertEqual({s['source_id'] for s in prepared['sources']}, {p['source_id'] for p in prepared['context']['academic_evidence']})

    def test_legacy_deep_resynthesis_uses_only_saved_academic_data(self):
        from research.evidence import persist_evidence
        from agents.consensus_schemas import normalize_search
        selection = select_configuration('deep')
        selection.update(pipeline='legacy', judge_model='retired/model')
        query = save_question('Sleep memory', selection=selection)
        persist_evidence(query, normalize_search(json.loads(search_response().text), query.question))
        original_citation_ids = list(query.citations.values_list('pk', flat=True))
        query.status = 'completed'
        query.save()
        retry_research(query, 'synthesis')
        self.get.assert_not_called()
        self.assertEqual(self.post.call_count, 1)
        self.assertTrue(query.final_response.answer)
        self.assertEqual(self.contexts[0]['academic_evidence'][0]['source_providers'], ['consensus'])
        self.assertEqual(list(query.citations.values_list('pk', flat=True)), original_citation_ids)

    def test_deep_routing_reuses_existing_planner_models(self):
        self.assertEqual(select_configuration('deep')['models'], select_configuration('balanced')['models'])
