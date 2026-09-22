import json
from datetime import timedelta
from io import StringIO
from unittest.mock import Mock, patch
from uuid import uuid4

import requests
from django.conf import settings
from django.core.management import call_command
from django.test import TestCase, SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from agents.budgets import budget_for
from agents.error_categories import classify
from agents.exceptions import AgentError
from agents.schemas import normalize_content
from agents.test_consensus import search_response, search_fixture, paper_fixture
from agents.test_synthesizer import judge_response, judge_result
from agents.tests import api_response, research_answer
from chats.models import Message
from chats.services import save_question
from .execution import claim, ownership, touch, stale_queries, LeaseLost, ResearchBusy
from .filtering import select_papers
from .models import AgentResponse, ResearchQuery, DeveloperRating, Citation
from .preparation import prepare_evidence
from .recovery import recover_query
from .services import run_agent, run_research, retry_research
from .support import annotate_support


@override_settings(OPENROUTER_API_KEY="private-router-key", CONSENSUS_API_KEY="private-consensus-key",
                   OPENROUTER_MAX_RETRIES=0, CONSENSUS_MAX_RETRIES=0, PROVIDER_RETRY_BACKOFF=0,
                   PROVIDER_BUDGETS={}, RESEARCH_DIAGNOSTICS_ENABLED=True)
class ReliabilityTests(TestCase):
    def setUp(self):
        self.query = save_question("Does sleep improve memory?")
        self.post_patch = patch("requests.post", side_effect=self.respond)
        self.get_patch = patch("requests.get", return_value=search_response())
        self.sleep_patch = patch("research.execution.time.sleep")
        self.post, self.get, self.sleep = self.post_patch.start(), self.get_patch.start(), self.sleep_patch.start()
        self.addCleanup(self.post_patch.stop)
        self.addCleanup(self.get_patch.stop)
        self.addCleanup(self.sleep_patch.stop)

    def respond(self, url, **kwargs):
        if kwargs["json"]["response_format"]["json_schema"]["name"] == "final_synthesis":
            return judge_response()
        return api_response()

    def stale(self, query=None, plan=None):
        query = query or self.query
        query.status = "processing"
        query.stage = "COLLECTING_AGENTS"
        query.execution_token = uuid4()
        query.lease_expires_at = timezone.now() - timedelta(seconds=1)
        query.execution_data = plan or {}
        query.save()
        return query

    @override_settings(OPENROUTER_MAX_RETRIES=2, PROVIDER_RETRY_BACKOFF=2)
    def test_transient_timeout_retry_preserves_both_attempts(self):
        self.post.side_effect = [requests.Timeout("private-router-key"), api_response()]
        result = run_agent(self.query, settings.OPENROUTER_MODELS[0])
        self.assertTrue(result.succeeded)
        self.assertEqual(self.post.call_count, 2)
        self.assertEqual(self.query.agent_responses.count(), 2)
        self.assertEqual(self.query.agent_responses.earliest("pk").normalized_response["error"]["category"], "timeout")
        self.sleep.assert_called_once_with(2)

    @override_settings(OPENROUTER_MAX_RETRIES=2)
    def test_authentication_and_invalid_request_do_not_retry(self):
        for status, category in [(401, "authentication"), (403, "authentication"), (400, "invalid_request"), (402, "invalid_request")]:
            with self.subTest(status=status):
                self.post.reset_mock()
                self.post.side_effect = None
                self.post.return_value = api_response(status=status, body={"error": {"message": "private-router-key"}})
                response = run_agent(self.query, settings.OPENROUTER_MODELS[0])
                self.assertEqual(response.normalized_response["error"]["category"], category)
                self.assertEqual(self.post.call_count, 1)
        self.sleep.assert_not_called()

    @override_settings(OPENROUTER_MAX_RETRIES=2)
    def test_rate_limit_and_unavailable_retry(self):
        self.post.side_effect = [api_response(status=429, body={}), api_response(status=503, body={}), api_response()]
        result = run_agent(self.query, settings.OPENROUTER_MODELS[0])
        self.assertTrue(result.succeeded)
        self.assertEqual(self.query.agent_responses.count(), 3)
        errors = [r.normalized_response["error"]["category"] for r in self.query.agent_responses.order_by("pk")[:2]]
        self.assertEqual(errors, ["rate_limit", "provider_unavailable"])

    @override_settings(OPENROUTER_MAX_RETRIES=2, PROVIDER_RETRY_BACKOFF=30)
    def test_retry_exhaustion_and_bounded_backoff(self):
        self.post.side_effect = requests.ConnectionError("private-router-key")
        response = run_agent(self.query, settings.OPENROUTER_MODELS[0])
        self.assertFalse(response.succeeded)
        self.assertEqual(self.post.call_count, 3)
        self.assertEqual([call.args[0] for call in self.sleep.call_args_list], [30, 30])

    @override_settings(OPENROUTER_MAX_RETRIES=2)
    def test_malformed_output_not_retried(self):
        self.post.side_effect = None
        self.post.return_value = api_response("not JSON")
        response = run_agent(self.query, settings.OPENROUTER_MODELS[0])
        self.assertFalse(response.succeeded)
        self.post.assert_called_once()

    @override_settings(CONSENSUS_MAX_RETRIES=1)
    def test_consensus_transient_retry(self):
        from .evidence import run_consensus
        self.get.side_effect = [requests.Timeout(), search_response()]
        response = run_consensus(self.query)
        self.assertTrue(response.succeeded)
        self.assertEqual(self.query.agent_responses.count(), 2)

    @override_settings(PROVIDER_BUDGETS={"synthesis": {"max_retries": 1}})
    def test_judge_transient_retry_history(self):
        from .synthesis import synthesize_research
        run_agent(self.query, settings.OPENROUTER_MODELS[0])
        self.post.side_effect = [Mock(status_code=503, text="{}"), judge_response()]
        final = synthesize_research(self.query)
        self.assertTrue(final.answer)
        self.assertEqual(self.query.synthesis_attempts.count(), 2)
        self.assertEqual(self.query.synthesis_attempts.earliest("pk").synthesis_data["error"]["category"], "provider_unavailable")

    @override_settings(PROVIDER_BUDGETS={"gpt": {"timeout": 7, "max_tokens": 123, "max_retries": 0}, "consensus": {"timeout": 8}})
    def test_individual_request_budgets_reach_http(self):
        run_agent(self.query, settings.OPENROUTER_MODELS[0])
        self.assertEqual(self.post.call_args.kwargs["timeout"], (5, 7))
        self.assertEqual(self.post.call_args.kwargs["json"]["max_tokens"], 123)

    @override_settings(PROVIDER_BUDGETS={"claude": {"enabled": False}, "consensus": {"enabled": False}})
    def test_disabled_providers_are_not_called_or_retried(self):
        run_research(self.query)
        self.assertEqual(self.post.call_count, 3)
        self.get.assert_not_called()
        self.assertFalse(self.query.agent_responses.filter(provider_key="claude").exists())
        with self.assertRaises(ValueError):
            retry_research(self.query, "claude")
        self.assertEqual(self.query.stage, "COMPLETED")
        page = self.client.get(reverse("chats:detail", args=[self.query.chat_id]))
        label = next(m.get("display_label", m["label"]) for m in settings.OPENROUTER_MODELS if m["label"] == "Claude")
        self.assertContains(page, f"Disabled providers: {label}, Consensus")

    def test_partial_completion_compatible_status_and_stage(self):
        calls = [api_response(), requests.Timeout(), api_response(), judge_response()]
        self.post.side_effect = calls
        run_research(self.query)
        self.assertEqual(self.query.status, "completed")
        self.assertEqual(self.query.stage, "PARTIAL")
        self.assertEqual(self.query.agent_responses.count(), 4)

    def test_failed_synthesis_stage_is_partial(self):
        self.post.side_effect = [api_response(), api_response(), api_response(), requests.Timeout()]
        run_research(self.query)
        self.assertEqual(self.query.stage, "PARTIAL")
        self.assertEqual(self.query.synthesis_attempts.count(), 1)

    def test_execution_stages_visible_during_calls(self):
        stages = []
        def respond(url, **kwargs):
            stages.append(ResearchQuery.objects.get(pk=self.query.pk).stage)
            return self.respond(url, **kwargs)
        self.post.side_effect = respond
        run_research(self.query)
        self.assertEqual(stages, ["COLLECTING_AGENTS"] * 3 + ["SYNTHESIZING"])
        self.assertEqual(self.query.stage, "COMPLETED")

    def test_stale_detection_excludes_live_and_completed(self):
        self.stale()
        current = save_question("Current")
        current.status = "processing"
        current.lease_expires_at = timezone.now() + timedelta(minutes=15)
        current.save()
        done = save_question("Done")
        done.status = "completed"
        done.lease_expires_at = timezone.now() - timedelta(minutes=10)
        done.save()
        self.assertEqual(list(stale_queries().values_list("pk", flat=True)), [self.query.pk])

    def test_legacy_processing_without_lease_is_recoverable(self):
        self.query.status = "processing"
        self.query.started_at = timezone.now() - timedelta(hours=1)
        self.query.save()
        self.assertTrue(stale_queries().filter(pk=self.query.pk).exists())

    def test_dry_run_recovery_does_not_mutate_or_call_apis(self):
        self.stale()
        token = self.query.execution_token
        out = StringIO()
        call_command("recover_research", stdout=out)
        self.post.assert_not_called()
        self.get.assert_not_called()
        self.query.refresh_from_db()
        self.assertEqual(self.query.execution_token, token)
        self.assertEqual(self.query.status, "processing")
        self.assertIn("Dry run", out.getvalue())

    def test_live_recovery_skips_successful_provider(self):
        saved = run_agent(self.query, settings.OPENROUTER_MODELS[0])
        self.post.reset_mock()
        self.stale()
        call_command("recover_research", live=True, query_id=self.query.pk, stdout=StringIO())
        self.query.refresh_from_db()
        self.assertEqual(self.query.stage, "COMPLETED")
        self.assertEqual(self.post.call_count, 3)
        self.assertEqual(self.query.agent_responses.filter(provider_key="gpt").get().pk, saved.pk)
        self.assertTrue(self.query.execution_data["recovered"])

    def test_recovery_synthesis_only_when_collection_done(self):
        from .evidence import run_consensus
        for model in settings.OPENROUTER_MODELS:
            run_agent(self.query, model)
        run_consensus(self.query)
        self.post.reset_mock()
        self.get.reset_mock()
        self.stale()
        recover_query(self.query)
        self.post.assert_called_once()
        self.get.assert_not_called()
        self.assertEqual(self.query.agent_responses.count(), 4)

    def test_recovery_does_not_repeat_matching_successful_judge(self):
        run_research(self.query)
        self.stale(plan={"targets": ["gpt", "claude", "gemini", "consensus"], "synthesis": True})
        self.post.reset_mock()
        self.get.reset_mock()
        recover_query(self.query)
        self.post.assert_not_called()
        self.get.assert_not_called()
        self.assertEqual(self.query.synthesis_attempts.count(), 1)

    def test_recovery_respects_original_synthesis_only_plan(self):
        run_agent(self.query, settings.OPENROUTER_MODELS[0])
        self.stale(plan={"targets": [], "synthesis": True, "judge_model": settings.SYNTHESIZER_MODEL})
        self.post.reset_mock()
        recover_query(self.query)
        self.post.assert_called_once()
        self.get.assert_not_called()

    def test_recovery_skip_synthesis_makes_no_judge_call(self):
        self.stale()
        recover_query(self.query, resume_synthesis=False)
        self.assertEqual(self.post.call_count, 3)
        self.assertFalse(self.query.synthesis_attempts.exists())
        self.assertEqual(self.query.stage, "PARTIAL")

    def test_nonstale_recovery_cannot_claim(self):
        self.query.status = "processing"
        self.query.lease_expires_at = timezone.now() + timedelta(minutes=15)
        self.query.save()
        with self.assertRaises(ResearchBusy):
            recover_query(self.query)
        self.post.assert_not_called()

    def test_old_execution_is_fenced_before_persistence(self):
        plan = {"targets": ["gpt"], "synthesis": False}
        token = claim(self.query, plan)
        def lose_lease(*args, **kwargs):
            ResearchQuery.objects.filter(pk=self.query.pk).update(execution_token=uuid4())
            return api_response()
        self.post.side_effect = lose_lease
        with ownership(self.query, token), self.assertRaises(LeaseLost):
            run_agent(self.query, settings.OPENROUTER_MODELS[0])
        self.assertFalse(self.query.agent_responses.exists())

    def test_expired_owner_cannot_extend_its_lease(self):
        token = claim(self.query, {"targets": [], "synthesis": False})
        ResearchQuery.objects.filter(pk=self.query.pk).update(lease_expires_at=timezone.now() - timedelta(seconds=1))
        with ownership(self.query, token), self.assertRaises(LeaseLost):
            touch(self.query)

    def test_duplicate_submission_reuses_query_and_messages(self):
        key = str(uuid4())
        data = {"question": "Same research question", "submission_key": key}
        first = self.client.post(reverse("chats:submit"), data)
        second = self.client.post(reverse("chats:submit"), data)
        self.assertEqual(first.url, second.url)
        self.assertEqual(ResearchQuery.objects.filter(submission_key=key).count(), 1)
        self.assertEqual(Message.objects.filter(content="Same research question").count(), 1)
        self.assertEqual(self.post.call_count, 4)
        self.assertEqual(self.client.post(reverse("chats:submit"), {**data, "question": "Different"}).status_code, 400)

    def test_duplicate_retry_action_is_not_replayed(self):
        key = str(uuid4())
        url = reverse("chats:retry", args=[self.query.pk])
        data = {"target": "gpt", "action_key": key}
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.post.assert_called_once()
        self.assertEqual(self.query.actions.count(), 1)
        self.assertEqual(self.client.post(url, {**data, "target": "claude"}).status_code, 400)

    def test_structured_logs_contain_safe_diagnostics(self):
        self.post.side_effect = requests.Timeout("private-router-key PRIVATE PROMPT")
        with self.assertLogs("research.execution", "INFO") as captured:
            run_agent(self.query, settings.OPENROUTER_MODELS[0])
        entry = json.loads(captured.records[0].getMessage())
        self.assertEqual(entry["query_id"], self.query.pk)
        self.assertEqual(entry["error_category"], "timeout")
        self.assertEqual(entry["attempt_number"], 1)
        self.assertNotIn("private-router-key", str(captured.output))
        self.assertNotIn(self.query.question, str(captured.output))

    def test_manual_rating_validation_snapshot_and_comparison(self):
        run_research(self.query)
        final = self.query.final_response
        url = reverse("chats:rate", args=[self.query.pk])
        data = {"answer_quality": 4, "citation_quality": 3, "evidence_relevance": 5, "synthesis_quality": 4,
                "notes": "Review note private-router-key", "synthesis_attempt": final.active_attempt_id}
        self.assertEqual(self.client.post(url, {**data, "answer_quality": 6}).status_code, 400)
        self.assertEqual(self.client.post(url, data).status_code, 302)
        rating = DeveloperRating.objects.get()
        self.assertEqual(rating.synthesis_attempt_id, final.active_attempt_id)
        self.assertNotIn("private-router-key", rating.notes)
        page = self.client.get(reverse("chats:evaluations"), {"question": "sleep"})
        self.assertContains(page, "Compare research runs")
        self.assertContains(page, "Answer 4/5")
        self.assertEqual(self.post.call_count, 4)

    @override_settings(RESEARCH_DIAGNOSTICS_ENABLED=False)
    def test_rating_disabled_with_diagnostics(self):
        self.assertEqual(self.client.post(reverse("chats:rate", args=[self.query.pk]), {}).status_code, 404)

    def test_duplicate_evaluation_post_is_not_replayed(self):
        data = {"submission_key": str(uuid4())}
        url = reverse("chats:run_evaluation", args=[0])
        first = self.client.post(url, data)
        second = self.client.post(url, data)
        self.assertEqual(first.url, second.url)
        self.assertEqual(self.post.call_count, 4)
        self.assertEqual(ResearchQuery.objects.exclude(evaluation_case=None).count(), 1)

    def test_recovered_query_keeps_incomplete_metrics_notice(self):
        self.stale()
        recover_query(self.query)
        retry_research(self.query, "synthesis")
        self.assertTrue(self.query.execution_data["recovered"])
        self.assertTrue(self.query.execution_data["duration_incomplete"])

    def test_strong_agent_only_judge_claim_has_stored_mismatch(self):
        from .synthesis import synthesize_research
        agent = run_agent(self.query, settings.OPENROUTER_MODELS[0])
        result = judge_result([f"A{agent.pk}"])
        result["key_findings"][0]["evidence_strength"] = "strong"
        self.post.side_effect = None
        self.post.return_value = judge_response(result)
        final = synthesize_research(self.query)
        finding = final.synthesis_data["key_findings"][0]
        self.assertEqual(finding["declared_evidence_strength"], "strong")
        self.assertTrue(finding["support_review"]["strong_evidence_mismatch"])
        self.assertEqual(final.answer, result["final_answer"])

    @override_settings(MIN_SUCCESSFUL_AGENTS=3, MIN_VALID_SOURCES_FOR_SYNTHESIS=4)
    def test_thresholds_warn_without_blocking_usable_evidence(self):
        run_agent(self.query, settings.OPENROUTER_MODELS[0])
        prepared = prepare_evidence(self.query)
        self.assertTrue(prepared["usable"])
        self.assertGreaterEqual(len(prepared["context"]["evidence_thresholds"]["warnings"]), 2)


class EvidenceReliabilityTests(TestCase):
    def setUp(self):
        self.query = save_question("sleep and memory")

    def candidate(self, title, doi, **extra):
        citation = Citation.objects.create(research_query=self.query, title=title, url="", source_name="Journal")
        metadata = {"title": title, "doi": doi, "abstract": "Sleep and memory study.", **extra}
        return citation, metadata

    def test_exact_identity_deduplication(self):
        first = self.candidate("Sleep and memory", "10.1234/same")
        duplicate = self.candidate("Same paper title variant", "10.1234/same")
        notices = []
        unique, selected = select_papers([first, duplicate], self.query.question, notices)
        self.assertEqual(len(unique), 1)
        self.assertEqual(len(selected), 1)
        self.assertIn("Duplicate", notices[0]["reason"])

    def test_relevance_and_metadata_prioritization(self):
        low = self.candidate("Sleep", "10.1234/low", relevance_score=0.1)
        rich = self.candidate("Sleep memory", "10.1234/rich", relevance_score=0.9, authors=["Author"], year=2025, journal="Journal")
        _, selected = select_papers([low, rich], self.query.question, [])
        self.assertEqual(selected[0][0].pk, rich[0].pk)
        self.assertNotIn("peer_review", selected[0][2]["components"])

    def test_non_preprint_flag_does_not_invent_peer_review(self):
        from .scoring import score_paper
        result = score_paper({"api_metadata": {"is_preprint": False}}, [], 2026)
        self.assertNotIn("peer_review", result["components"])

    @override_settings(SYNTHESIS_MAX_PAPERS=2)
    def test_diversity_penalizes_similar_titles_without_deleting_papers(self):
        first = self.candidate("Sleep and memory in adults", "10.1234/first")
        near = self.candidate("Sleep and memory in adults", "10.1234/near")
        different = self.candidate("Memory consolidation mechanisms", "10.1234/different")
        unique, selected = select_papers([first, near, different], self.query.question, [])
        self.assertEqual(len(unique), 3)
        self.assertEqual([row[0].pk for row in selected], [first[0].pk, different[0].pk])


class SupportClassificationTests(SimpleTestCase):
    def prepared(self):
        return {"sources": [{"source_id": "C1", "kind": "academic"}, {"source_id": "A1", "kind": "agent"}],
                "context": {"academic_evidence": [{"source_id": "C1", "abstract_or_snippet": "Sleep improves memory in this small study.", "takeaway": ""}]}}

    @override_settings(MIN_CONSENSUS_PAPERS_FOR_STRONG_EVIDENCE=2)
    def test_support_statuses_and_strong_mismatch(self):
        for ids, strength, claim_text, expected in [
            (["C1"], "moderate", "Sleep improves memory", "academically_supported"),
            (["A1"], "strong", "Sleep improves memory", "agent_supported_only"),
            (["C1"], "conflicting", "Sleep improves memory", "conflicting"),
            (["C1"], "moderate", "Economic growth accelerates", "weak_support"),
            (["C999"], "strong", "Sleep improves memory", "no_traceable_support"),
        ]:
            with self.subTest(expected=expected):
                result = {"key_findings": [{"claim": claim_text, "evidence_strength": strength, "supporting_source_ids": ids}]}
                annotate_support(result, self.prepared())
                review = result["key_findings"][0]["support_review"]
                self.assertEqual(review["status"], expected)
                if strength == "strong":
                    self.assertTrue(review["strong_evidence_mismatch"])
                    self.assertEqual(review["diagnostic_evidence_strength"], "limited")
                self.assertEqual(result["key_findings"][0]["evidence_strength"], strength)

    def test_categories_do_not_use_exception_text(self):
        self.assertEqual(classify("connection"), "network_error")
        self.assertEqual(classify("api_error", 503), "provider_unavailable")
        self.assertEqual(classify("unrecognized"), "unknown")
