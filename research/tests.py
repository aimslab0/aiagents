import json
from unittest.mock import patch

import requests
from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse

from agents.tests import api_response, research_answer
from chats.services import save_question

from .models import AgentResponse, Citation, FinalResponse, ResearchQuery
from .services import run_research


@override_settings(OPENROUTER_API_KEY="test-openrouter-secret", CONSENSUS_API_KEY="")
class ResearchRunnerTests(TestCase):
    def setUp(self):
        synthesis = patch("research.services.synthesize_research")
        synthesis.start()
        self.addCleanup(synthesis.stop)
        self.query = save_question("Does sleep affect memory?")
        mocked_http = patch("agents.openrouter.requests.post")
        self.http = mocked_http.start()
        self.addCleanup(mocked_http.stop)

    def test_success_persists_each_model_and_status_transitions(self):
        self.assertEqual(self.query.status, ResearchQuery.Status.PENDING)

        def respond(*args, **kwargs):
            self.query.refresh_from_db()
            self.assertEqual(self.query.status, ResearchQuery.Status.PROCESSING)
            self.assertIsNone(self.query.completed_at)
            # Earlier models are committed independently, before the next call.
            self.assertEqual(self.query.agent_responses.count(), self.http.call_count - 1)
            return api_response()

        self.http.side_effect = respond
        run_research(self.query)
        self.query.refresh_from_db()
        self.assertEqual(self.query.status, ResearchQuery.Status.COMPLETED)
        self.assertIsNotNone(self.query.completed_at)
        responses = list(self.query.agent_responses.filter(provider="openrouter").order_by("pk"))
        self.assertEqual([r.model_name for r in responses], [m["id"] for m in settings.OPENROUTER_MODELS])
        for result in responses:
            self.assertEqual(result.provider, "openrouter")
            self.assertEqual(result.confidence, 72)
            self.assertEqual(json.loads(result.raw_response), result.normalized_response["raw_response"])
            self.assertIsNone(result.normalized_response["error"])
        questions = [call.kwargs["json"]["messages"][1]["content"] for call in self.http.call_args_list]
        self.assertEqual(questions, [self.query.question] * 3)
        self.assertFalse(Citation.objects.exists())
        self.assertFalse(FinalResponse.objects.exists())
        self.assertFalse(self.query.chat.messages.filter(role="assistant").exists())

    def test_one_model_fails_others_still_saved_and_rendered(self):
        self.http.side_effect = [api_response(), requests.Timeout("private upstream details"), api_response()]
        run_research(self.query)
        self.assertEqual(self.query.status, ResearchQuery.Status.COMPLETED)
        responses = list(self.query.agent_responses.filter(provider="openrouter").order_by("pk"))
        self.assertEqual(len(responses), 3)
        self.assertIsNone(responses[0].normalized_response["error"])
        self.assertEqual(responses[1].normalized_response["error"]["code"], "timeout")
        self.assertIsNone(responses[1].confidence)
        self.assertIsNone(responses[2].normalized_response["error"])
        page = self.client.get(reverse("chats:detail", args=[self.query.chat_id]))
        self.assertContains(page, "This model timed out")
        self.assertContains(page, research_answer()["answer"], count=2)
        self.assertNotContains(page, "private upstream details")

    def test_all_fail_sets_failed_and_completion_time(self):
        self.http.return_value = api_response(status=503, body={"error": {"message": "Unavailable"}})
        run_research(self.query)
        self.query.refresh_from_db()
        self.assertEqual(self.query.status, ResearchQuery.Status.FAILED)
        self.assertIsNotNone(self.query.completed_at)
        self.assertEqual(self.query.agent_responses.filter(provider="openrouter").count(), 3)

    def test_malformed_output_is_saved_with_raw_response(self):
        self.http.side_effect = [api_response("invalid JSON"), api_response(), api_response()]
        run_research(self.query)
        failure = self.query.agent_responses.order_by("pk").first()
        self.assertIn("invalid JSON", failure.raw_response)
        self.assertEqual(failure.normalized_response["error"]["code"], "malformed_response")
        self.assertEqual(self.query.status, ResearchQuery.Status.COMPLETED)

    def test_unexpected_client_error_is_isolated_and_sanitized(self):
        self.http.side_effect = [ValueError("test-openrouter-secret"), api_response(), api_response()]
        run_research(self.query)
        self.assertEqual(self.query.status, ResearchQuery.Status.COMPLETED)
        self.assertEqual(self.query.agent_responses.filter(provider="openrouter").count(), 3)
        self.assertNotIn("test-openrouter-secret", str(list(AgentResponse.objects.values())))

    def test_completed_and_processing_queries_are_not_executed_twice(self):
        self.http.return_value = api_response()
        run_research(self.query)
        run_research(self.query)
        self.assertEqual(self.http.call_count, 3)
        self.assertEqual(self.query.agent_responses.filter(provider="openrouter").count(), 3)
        self.query.status = ResearchQuery.Status.PROCESSING
        self.query.save(update_fields=["status"])
        run_research(self.query)
        self.assertEqual(self.http.call_count, 3)

    def test_error_body_credentials_not_stored_or_rendered(self):
        self.http.return_value = api_response(status=401, body={"error": {"message": "test-openrouter-secret"}, "authorization": "Bearer test-openrouter-secret"})
        run_research(self.query)
        self.assertNotIn("test-openrouter-secret", str(list(AgentResponse.objects.values())))
        page = self.client.get(reverse("chats:detail", args=[self.query.chat_id]))
        self.assertNotContains(page, "test-openrouter-secret")
        self.assertContains(page, "OpenRouter rejected the server credentials")

    def test_output_is_escaped_and_citations_render_safely(self):
        answer = research_answer(answer="<script>alert(1)</script>", citations=[{"title": "<b>Source</b>", "url": "javascript:alert(1)", "source_name": "Journal"}])
        self.http.return_value = api_response(json.dumps(answer))
        run_research(self.query)
        page = self.client.get(reverse("chats:detail", args=[self.query.chat_id]))
        self.assertContains(page, "&lt;script&gt;")
        self.assertContains(page, "&lt;b&gt;Source&lt;/b&gt;")
        self.assertNotContains(page, "javascript:")
        self.assertNotContains(page, "<script>alert")
