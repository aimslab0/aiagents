import json
from decimal import Decimal
from unittest.mock import Mock, patch

import requests
from django.conf import settings
from django.test import TestCase, override_settings

from agents.test_synthesizer import judge_response, judge_result
from chats.services import save_question
from .test_synthesis import EvidenceFixtures
from .preparation import prepare_evidence
from .synthesis import synthesize_research


@override_settings(OPENROUTER_API_KEY="mock-key", OPENROUTER_FREE_TEST_MODE=False, OPENROUTER_MAX_RETRIES=4)
class BalancedFallbackTests(EvidenceFixtures, TestCase):
    def test_canonical_primary_setting_wins(self):
        from research_ai.model_config import configure_production
        _, judge = configure_production({'BALANCED_SYNTHESIZER_PRIMARY': 'configured/primary', 'PRIMARY_SYNTHESIZER_MODEL': 'legacy/primary'})
        self.assertEqual(judge, 'configured/primary')

    def fixture(self):
        self.query = save_question("Does sleep improve memory?")
        paper = self.paper()
        prepared = prepare_evidence(self.query)
        prepared['context']['research_plan'] = {}
        self.query.execution_data = {'selection': {'mode': 'balanced', 'pipeline': 'plan_and_solve', 'free_test': False,
            'judge_model': settings.BALANCED_SYNTHESIZER_PRIMARY, 'fallback_judge': settings.BALANCED_SYNTHESIZER_FALLBACK}}
        self.query.save()
        return prepared, judge_result([f'C{paper.pk}'])

    def test_success_low_confidence_and_disagreement_never_fall_back(self):
        for updates in ({}, {'overall_confidence': 1}, {'disagreements': ['Studies report conflicting findings.']}):
            prepared, result = self.fixture()
            result.update(updates)
            with patch('research.synthesis.prepare_evidence', return_value=prepared), patch('agents.synthesizer.requests.post', return_value=judge_response(result)) as post:
                final = synthesize_research(self.query)
            post.assert_called_once()
            self.assertEqual(final.active_attempt.model_name, settings.BALANCED_SYNTHESIZER_PRIMARY)
            self.assertFalse(final.synthesis_data['diagnostics']['balanced_routing']['fallback_triggered'])

    def test_technical_failures_fallback_once_with_identical_context(self):
        failures = [
            Mock(status_code=200, text=json.dumps({'choices': [{'message': {'content': 'bad json'}}]})),
            Mock(status_code=200, text=json.dumps({'choices': [{'finish_reason': 'length'}]})),
            requests.Timeout(), requests.ConnectionError(),
            Mock(status_code=503, text='{}'), Mock(status_code=404, text='{}'),
            Mock(status_code=400, text=json.dumps({'error': {'message': 'Invalid JSON schema'}})),
            judge_response({'final_answer': 'Missing required schema fields'}),
        ]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                prepared, result = self.fixture()
                with patch('research.synthesis.prepare_evidence', return_value=prepared) as prepare, patch('agents.synthesizer.requests.post', side_effect=[failure, judge_response(result)]) as post:
                    final = synthesize_research(self.query)
                prepare.assert_called_once()
                self.assertEqual(post.call_count, 2)
                calls = [c.kwargs['json'] for c in post.call_args_list]
                self.assertEqual([c['model'] for c in calls], [settings.BALANCED_SYNTHESIZER_PRIMARY, settings.BALANCED_SYNTHESIZER_FALLBACK])
                self.assertEqual(calls[0]['messages'], calls[1]['messages'])
                self.assertEqual(calls[0]['response_format'], calls[1]['response_format'])
                self.assertEqual(self.query.synthesis_attempts.count(), 2)
                self.assertFalse(self.query.synthesis_attempts.earliest('pk').succeeded)
                self.assertEqual(final.active_attempt_id, self.query.synthesis_attempts.latest('pk').pk)
                self.assertEqual(final.active_attempt.model_name, settings.BALANCED_SYNTHESIZER_FALLBACK)
                self.assertTrue(final.synthesis_data['diagnostics']['balanced_routing']['fallback_triggered'])

    def test_both_fail_stop_after_two_attempts(self):
        prepared, _ = self.fixture()
        with patch('research.synthesis.prepare_evidence', return_value=prepared), patch('agents.synthesizer.requests.post', side_effect=requests.Timeout()) as post:
            final = synthesize_research(self.query)
        self.assertEqual(post.call_count, 2)
        self.assertEqual(self.query.synthesis_attempts.count(), 2)
        self.assertIsNone(final.synthesis_data['diagnostics']['balanced_routing']['accepted_model'])

    def test_combined_metrics_preserve_per_attempt_usage(self):
        prepared, result = self.fixture()
        failed = Mock(status_code=200, text=json.dumps({'usage': {'cost': 0.01, 'total_tokens': 20}, 'choices': [{'finish_reason': 'length'}]}))
        successful = judge_response(result)
        body = json.loads(successful.text)
        body['usage'] = {'cost': 0.02, 'total_tokens': 30}
        successful.text = json.dumps(body)
        with patch('research.synthesis.prepare_evidence', return_value=prepared), patch('agents.synthesizer.requests.post', side_effect=[failed, successful]):
            final = synthesize_research(self.query)
        attempts = list(self.query.synthesis_attempts.order_by('pk'))
        route = final.synthesis_data['diagnostics']['balanced_routing']
        self.assertEqual(Decimal(route['total_cost_usd']), Decimal('0.03'))
        self.assertEqual(route['total_latency_ms'], sum(a.latency_ms for a in attempts))
        self.assertEqual([a.total_tokens for a in attempts], [20, 30])

    def test_deep_and_free_routes_keep_original_runner(self):
        self.fixture()
        for selection in ({'mode': 'deep'}, {'mode': 'balanced', 'free_test': True}):
            self.query.execution_data['selection'] = selection
            with patch('research.synthesis.retry_call', return_value='original') as runner, patch('research.synthesis._synthesize_research') as call:
                self.assertEqual(synthesize_research(self.query, model_id=settings.BALANCED_SYNTHESIZER_PRIMARY), 'original')
            runner.assert_called_once()
            call.assert_not_called()

    def test_billing_authentication_and_invalid_citations_do_not_fallback(self):
        for response in (Mock(status_code=402, text='{}'), Mock(status_code=401, text='{}'), judge_response(judge_result(['C999999']))):
            prepared, _ = self.fixture()
            with patch('research.synthesis.prepare_evidence', return_value=prepared), patch('agents.synthesizer.requests.post', return_value=response) as post:
                final = synthesize_research(self.query)
            post.assert_called_once()
            self.assertFalse(final.answer)
