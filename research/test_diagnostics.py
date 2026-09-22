import json
from decimal import Decimal
from io import StringIO
from unittest.mock import Mock, patch

import requests
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, SimpleTestCase, override_settings
from django.urls import reverse

from agents.consensus_schemas import normalize_search
from agents.exceptions import AgentError, ConsensusError
from agents.schemas import normalize_content
from agents.synthesis_schemas import SynthesisError
from agents.test_consensus import paper_fixture, search_fixture, search_response
from agents.test_synthesizer import judge_result, judge_response
from agents.tests import research_answer, api_response
from chats.services import save_question
from .evidence import run_consensus
from .metrics import credentials_ready, research_metrics, select_attempts, usage_metrics
from .models import FinalResponse, ResearchQuery, SynthesisAttempt
from .preparation import prepare_evidence
from .services import run_agent, retry_research, ResearchBusy, run_research
from .synthesis import synthesize_research


@override_settings(RESEARCH_COST_TRACKING_ENABLED=True, OPENROUTER_MODEL_PRICING={})
class UsageTests(SimpleTestCase):
    def test_reported_cost_and_tokens(self):
        data = usage_metrics({"usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5, "cost": 0}}, "model")
        self.assertEqual(data["estimated_cost"], Decimal(0))
        self.assertEqual(data["total_tokens"], 5)
        self.assertEqual(data["cost_source"], "reported")

    def test_missing_or_invalid_usage_is_unknown(self):
        for raw in ({}, {"usage": None}, {"usage": {"cost": "NaN", "prompt_tokens": True}}, {"usage": {"cost": -1, "total_tokens": -1}}):
            data = usage_metrics(raw, "model")
            self.assertIsNone(data["estimated_cost"])
            self.assertIsNone(data["input_tokens"])

    @override_settings(OPENROUTER_MODEL_PRICING={"model": {"input_per_million": "2", "output_per_million": "4"}})
    def test_central_pricing_fallback_and_reported_priority(self):
        raw = {"usage": {"prompt_tokens": 100, "completion_tokens": 50}}
        self.assertEqual(usage_metrics(raw, "model")["estimated_cost"], Decimal("0.0004"))
        self.assertEqual(usage_metrics(raw, "model")["cost_source"], "configured")
        raw["usage"]["cost"] = "0.0001"
        self.assertEqual(usage_metrics(raw, "model")["estimated_cost"], Decimal("0.0001"))
        self.assertIsNone(usage_metrics({}, "model")["estimated_cost"])

    @override_settings(OPENROUTER_MODEL_PRICING={"model": {"input_per_million": 2, "output_per_million": 4}})
    def test_cache_pricing_is_not_guessed(self):
        self.assertIsNone(usage_metrics({"usage": {"prompt_tokens": 100, "completion_tokens": 20, "prompt_tokens_details": {"cached_tokens": 50}}}, "model")["estimated_cost"])

    @override_settings(RESEARCH_COST_TRACKING_ENABLED=False)
    def test_cost_disabled_retains_tokens(self):
        result = usage_metrics({"usage": {"prompt_tokens": 4, "cost": 0.2}}, "model")
        self.assertIsNone(result["estimated_cost"])
        self.assertEqual(result["input_tokens"], 4)

    @override_settings(OPENROUTER_API_KEY="secret-one", CONSENSUS_API_KEY="")
    def test_readiness_only_returns_booleans(self):
        self.assertEqual(credentials_ready(), {"OpenRouter": True, "Consensus": False})
        self.assertNotIn("secret-one", str(credentials_ready()))


@override_settings(RESEARCH_DIAGNOSTICS_ENABLED=True, RESEARCH_COST_TRACKING_ENABLED=True,
                   OPENROUTER_API_KEY="private-openrouter-key", CONSENSUS_API_KEY="private-consensus-key",
                   SYNTHESIZER_MODELS=["openai/gpt-5.4", "configured/alternate"], SYNTHESIZER_MODEL="openai/gpt-5.4")
class AttemptTests(TestCase):
    def setUp(self):
        self.query = save_question("Does sleep support memory?")
        self.model = settings.OPENROUTER_MODELS[0]
        self.agent = Mock()
        self.agent.research.return_value = normalize_content(json.dumps(research_answer()), self.model["id"], {"usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30, "cost": "0.002"}})
        self.consensus = Mock()
        self.consensus.search.return_value = normalize_search(search_fixture([paper_fixture()]), self.query.question)
        self.judge = Mock(raw_metadata={"usage": {"prompt_tokens": 20, "completion_tokens": 30, "total_tokens": 50, "cost": "0.003"}})
        self.judge.synthesize.return_value = json.dumps(judge_result())
        self.network = patch("requests.sessions.Session.request", side_effect=AssertionError("Unexpected network request"))
        self.network.start()
        self.addCleanup(self.network.stop)

    def test_retry_preserves_attempts_and_latest_success(self):
        first = run_agent(self.query, self.model, self.agent)
        retry_research(self.query, "gpt", client=self.agent)
        second = self.query.agent_responses.latest("pk")
        self.agent.research.side_effect = AgentError("timeout")
        retry_research(self.query, "gpt", client=self.agent)
        latest, active = select_attempts(self.query)
        self.assertEqual(self.query.agent_responses.count(), 3)
        self.assertEqual(active["gpt"].pk, second.pk)
        self.assertNotEqual(latest["gpt"].pk, second.pk)
        self.assertTrue(self.query.agent_responses.filter(pk=first.pk).exists())
        prepared = prepare_evidence(self.query)
        self.assertEqual(prepared["context"]["agent_findings"][0]["source_id"], f"A{second.pk}")

    def test_retry_failed_only_and_no_judge(self):
        for model in settings.OPENROUTER_MODELS:
            run_agent(self.query, model, self.agent)
        run_consensus(self.query, self.consensus)
        self.agent.research.side_effect = AgentError("timeout")
        run_agent(self.query, settings.OPENROUTER_MODELS[1], self.agent)
        self.agent.research.side_effect = None
        self.agent.reset_mock()
        retry_research(self.query, "failed", client=self.agent, consensus_client=self.consensus, synthesis_client=self.judge)
        self.agent.research.assert_called_once_with(self.query.question, settings.OPENROUTER_MODELS[1]["id"])
        self.consensus.search.assert_called_once()
        self.judge.synthesize.assert_not_called()

    def test_synthesis_only_reuses_evidence_and_preserves_history(self):
        run_agent(self.query, self.model, self.agent)
        run_consensus(self.query, self.consensus)
        retry_research(self.query, "synthesis", synthesis_client=self.judge)
        accepted = FinalResponse.objects.get(research_query=self.query)
        self.judge.synthesize.side_effect = SynthesisError("timeout")
        retry_research(self.query, "synthesis", synthesis_client=self.judge)
        accepted.refresh_from_db()
        self.assertEqual(self.query.synthesis_attempts.count(), 2)
        self.assertEqual(accepted.active_attempt_id, self.query.synthesis_attempts.earliest("pk").pk)
        self.agent.research.assert_called_once()
        self.consensus.search.assert_called_once()
        self.assertTrue(accepted.answer)

    def test_successful_resynthesis_keeps_previous_answer(self):
        run_agent(self.query, self.model, self.agent)
        first = synthesize_research(self.query, self.judge)
        old_answer = first.answer
        self.judge.synthesize.return_value = json.dumps(judge_result(final_answer="Revised conclusion."))
        second = synthesize_research(self.query, self.judge)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(self.query.synthesis_attempts.earliest("pk").answer, old_answer)
        self.assertEqual(second.answer, "Revised conclusion.")
        self.assertEqual(second.active_attempt_id, self.query.synthesis_attempts.latest("pk").pk)

    def test_alternate_judge_is_allowlisted_and_used(self):
        run_agent(self.query, self.model, self.agent)
        with patch("agents.synthesizer.requests.post", return_value=judge_response()) as http:
            retry_research(self.query, "synthesis", model_id="configured/alternate")
        self.assertEqual(http.call_args.kwargs["json"]["model"], "configured/alternate")
        self.assertEqual(self.query.synthesis_attempts.get().model_name, "configured/alternate")
        with self.assertRaises(ValueError):
            retry_research(self.query, "synthesis", model_id="arbitrary/model")
        self.assertEqual(self.query.synthesis_attempts.count(), 1)

    def test_metrics_latency_tokens_and_total_cost(self):
        with patch("research.metrics.perf_counter", side_effect=[10, 10.125]):
            response = run_agent(self.query, self.model, self.agent)
        response.refresh_from_db()
        self.assertEqual(response.latency_ms, 125)
        self.assertIsNotNone(response.started_at)
        self.assertIsNotNone(response.completed_at)
        self.assertEqual((response.input_tokens, response.output_tokens, response.total_tokens), (10, 20, 30))
        synthesize_research(self.query, self.judge)
        judge = self.query.synthesis_attempts.get()
        self.assertEqual(judge.total_tokens, 50)
        self.assertEqual(judge.estimated_cost, Decimal("0.003"))
        self.assertEqual(research_metrics(self.query)["total_estimated_openrouter_cost"], Decimal("0.005"))

    def test_partial_unknown_cost_is_not_a_complete_total(self):
        run_agent(self.query, self.model, self.agent)
        self.agent.research.side_effect = AgentError("timeout")
        run_agent(self.query, self.model, self.agent)
        metrics = research_metrics(self.query)
        self.assertIsNone(metrics["total_estimated_openrouter_cost"])
        self.assertEqual(metrics["known_cost_subtotal"], Decimal("0.002"))
        self.assertEqual(metrics["failed_providers"], 1)

    def test_consensus_retry_tracks_papers_without_old_evidence_union(self):
        first = run_consensus(self.query, self.consensus)
        self.consensus.search.return_value = normalize_search(search_fixture([]), self.query.question)
        second = run_consensus(self.query, self.consensus)
        self.assertEqual(first.paper_count, 1)
        self.assertEqual(second.paper_count, 0)
        self.assertIsNotNone(second.latency_ms)
        self.assertEqual(self.query.agent_responses.count(), 2)
        self.assertEqual(self.query.citations.count(), 1)
        self.assertEqual(prepare_evidence(self.query)["context"]["academic_evidence"], [])
        self.consensus.search.side_effect = ConsensusError("timeout")
        third = run_consensus(self.query, self.consensus)
        self.assertFalse(third.succeeded)
        self.assertEqual(select_attempts(self.query)[1]["consensus"].pk, second.pk)

    def test_retried_paper_uses_new_attempt_text_and_preserves_old_snapshot(self):
        first = run_consensus(self.query, self.consensus)
        self.consensus.search.return_value = normalize_search(search_fixture([paper_fixture(abstract="Updated abstract from the latest search.")]), self.query.question)
        run_consensus(self.query, self.consensus)
        self.assertEqual(self.query.citations.count(), 1)
        prepared = prepare_evidence(self.query)
        self.assertEqual(prepared["context"]["academic_evidence"][0]["abstract_or_snippet"], "Updated abstract from the latest search.")
        first.refresh_from_db()
        self.assertNotEqual(first.normalized_response["papers"][0]["abstract"], "Updated abstract from the latest search.")

    def test_judge_usage_from_http_and_failed_json(self):
        run_agent(self.query, self.model, self.agent)
        envelope = {"usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16, "cost": "0.001"}, "choices": [{"message": {"content": "malformed"}, "finish_reason": "stop"}]}
        with patch("requests.post", return_value=Mock(status_code=200, text=json.dumps(envelope))):
            synthesize_research(self.query)
        attempt = self.query.synthesis_attempts.get()
        self.assertFalse(attempt.succeeded)
        self.assertEqual(attempt.total_tokens, 16)
        self.assertEqual(attempt.estimated_cost, Decimal("0.001"))

    def test_untraceable_inline_ids_recorded_in_failed_history(self):
        run_agent(self.query, self.model, self.agent)
        self.judge.synthesize.return_value = json.dumps(judge_result(final_answer="Invented citation [C9999]."))
        synthesize_research(self.query, self.judge)
        attempt = self.query.synthesis_attempts.get()
        self.assertFalse(attempt.succeeded)
        self.assertEqual(attempt.synthesis_data["validation"]["rejected_source_ids"], ["C9999"])

    def test_busy_query_does_not_make_calls(self):
        self.query.status = "processing"
        self.query.save()
        with self.assertRaises(ResearchBusy):
            retry_research(self.query, "gpt", client=self.agent)
        self.agent.research.assert_not_called()

    def test_research_total_duration_and_outcomes(self):
        run_research(self.query, client=self.agent, consensus_client=self.consensus, synthesis_client=self.judge)
        self.query.refresh_from_db()
        metrics = research_metrics(self.query)
        self.assertIsNotNone(self.query.started_at)
        self.assertIsNotNone(self.query.total_duration_ms)
        self.assertEqual(metrics["successful_providers"], 4)
        self.assertEqual(metrics["successful_agents"], 3)
        self.assertTrue(metrics["consensus_success"])
        self.assertTrue(metrics["synthesis_success"])
        before = self.query.total_duration_ms
        retry_research(self.query, "gpt", client=self.agent)
        self.assertGreaterEqual(self.query.total_duration_ms, before)

    def test_invalid_source_mapping_is_flagged(self):
        run_agent(self.query, self.model, self.agent)
        final = synthesize_research(self.query, self.judge)
        final.synthesis_data["sources"] = [{"source_id": "C9999", "kind": "academic", "citation_id": 9999, "title": "Not a stored source"}]
        final.synthesis_data["source_ids"] = ["C9999"]
        final.synthesis_data["key_findings"][0]["supporting_source_ids"] = ["C9999"]
        final.save()
        self.assertEqual(research_metrics(self.query)["valid_source_percent"], 0)
        page = self.client.get(reverse("chats:detail", args=[self.query.chat_id]))
        self.assertContains(page, "Invalid source mapping")
        self.assertContains(page, "Supporting sources selected by synthesizer")

    def test_rejected_ids_and_evaluation_diagnostics(self):
        response = run_agent(self.query, self.model, self.agent)
        data = judge_result([f"A{response.pk}"])
        data["source_ids"] = [f"A{response.pk}", "C9999"]
        self.judge.synthesize.return_value = json.dumps(data)
        final = synthesize_research(self.query, self.judge)
        self.assertEqual(final.synthesis_data["validation"]["rejected_source_ids"], ["C9999"])
        metrics = research_metrics(self.query)
        self.assertEqual(metrics["valid_source_percent"], 100)
        self.assertEqual(metrics["removed_source_ids"], 1)
        self.assertEqual(metrics["agreements_count"], 1)
        self.assertEqual(metrics["evidence_strength_distribution"], {"limited": 1})
        self.assertIsNone(metrics["judge_confidence"])

    def test_dashboard_controls_history_and_secret_safety(self):
        run_agent(self.query, self.model, self.agent)
        page = self.client.get(reverse("chats:detail", args=[self.query.chat_id]))
        for text in [*[f"Retry {m.get('display_label', m['label'])}" for m in settings.OPENROUTER_MODELS], "Retry Consensus", "Re-run Final Synthesis", "Retry Failed Providers", "Developer diagnostics", "Active", "Latest", "OpenRouter: Configured"]:
            self.assertContains(page, text)
        self.assertNotContains(page, "private-openrouter-key")
        self.assertNotContains(page, "private-consensus-key")

    @override_settings(RESEARCH_DIAGNOSTICS_ENABLED=False)
    def test_diagnostics_disabled(self):
        run_agent(self.query, self.model, self.agent)
        synthesize_research(self.query, self.judge)
        page = self.client.get(reverse("chats:detail", args=[self.query.chat_id]))
        for text in ["Developer diagnostics", "Credential readiness", "Evidence preparation details", "known_cost_subtotal"]:
            self.assertNotContains(page, text)
        self.assertEqual(self.client.get(reverse("chats:evaluations")).status_code, 404)

    def test_retry_http_post_validation_and_csrf(self):
        url = reverse("chats:retry", args=[self.query.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.client.post(url, {"target": "bad"}).status_code, 400)
        self.assertEqual(self.client.post(url, {"target": "synthesis", "judge_model": "not/allowed"}).status_code, 400)
        from django.test import Client
        self.assertEqual(Client(enforce_csrf_checks=True).post(url, {"target": "gpt"}).status_code, 403)
        self.query.status = "processing"
        self.query.save()
        self.assertEqual(self.client.post(url, {"target": "gpt"}).status_code, 409)

    def test_evaluation_page_has_no_network_and_explicit_run(self):
        page = self.client.get(reverse("chats:evaluations"))
        self.assertContains(page, "Run case (live APIs)", count=6)
        with patch("chats.views.run_research") as runner:
            response = self.client.post(reverse("chats:run_evaluation", args=[0]))
        self.assertEqual(response.status_code, 302)
        runner.assert_called_once()
        query = ResearchQuery.objects.latest("pk")
        self.assertEqual(query.evaluation_case["category"], "medical/scientific")
        self.assertEqual(query.question, query.evaluation_case["question"])

    def test_smoke_offline_never_calls_apis(self):
        out = StringIO()
        with patch("requests.post") as post, patch("requests.get") as get:
            call_command("research_smoketest", stdout=out)
        post.assert_not_called()
        get.assert_not_called()
        self.assertIn("PASS offline", out.getvalue())
        self.assertNotIn("private-openrouter-key", out.getvalue())

    def test_smoke_live_is_bounded_and_does_not_save_results(self):
        out = StringIO()
        with patch("requests.post", return_value=api_response()) as post, patch("requests.get", return_value=search_response()) as get:
            call_command("research_smoketest", live=True, stdout=out)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(get.call_count, 1)
        self.assertEqual(post.call_args.kwargs["json"]["max_tokens"], 512)
        self.assertEqual(get.call_args.kwargs["params"]["page_size"], 1)
        self.assertFalse(self.query.agent_responses.exists())
        self.assertFalse(SynthesisAttempt.objects.exists())

    def test_smoke_failure_prints_no_exception_or_secret(self):
        out = StringIO()
        with patch("requests.post", side_effect=requests.Timeout("private-openrouter-key")):
            with self.assertRaises(CommandError):
                call_command("research_smoketest", live=True, provider="openrouter", stdout=out)
        self.assertIn("FAIL OpenRouter", out.getvalue())
        self.assertNotIn("private-openrouter-key", out.getvalue())
