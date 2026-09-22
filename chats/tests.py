import json
from unittest.mock import Mock, patch

from django.contrib import admin
from django.conf import settings
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from research.models import AgentResponse, Citation, FinalResponse, ResearchQuery

from .models import Chat, Message
from .services import save_question


@override_settings(OPENROUTER_API_KEY="test-key", CONSENSUS_API_KEY="")
class DashboardTests(TestCase):
    def setUp(self):
        # Keep provider regression tests isolated; Step 4 has full-flow synthesis tests.
        synthesis = patch("research.services.synthesize_research")
        synthesis.start()
        self.addCleanup(synthesis.stop)
        mocked_http = patch("agents.openrouter.requests.post")
        self.http = mocked_http.start()
        self.addCleanup(mocked_http.stop)
        answer = {"answer": "A research answer", "key_findings": ["Finding"], "evidence": [], "citations": [], "confidence": 0}
        self.http.return_value = Mock(status_code=200, text=json.dumps({"choices": [{"message": {"content": json.dumps(answer)}, "finish_reason": "stop"}]}))

    def test_welcome_and_no_automatic_chat(self):
        response = self.client.get(reverse("chats:dashboard"))
        self.assertContains(response, "What would you like")
        self.assertEqual(Chat.objects.count(), 0)

    def test_new_chat_is_post_only_and_selects_created_chat(self):
        self.assertEqual(self.client.get(reverse("chats:new")).status_code, 405)
        response = self.client.post(reverse("chats:new"))
        chat = Chat.objects.get()
        self.assertRedirects(response, reverse("chats:detail", args=[chat.pk]))

    def test_first_question_creates_chat_message_and_completed_query(self):
        response = self.client.post(reverse("chats:submit"), {"question": "  How does sleep affect memory?  "})
        chat = Chat.objects.get()
        self.assertEqual(chat.title, "How does sleep affect memory?")
        self.assertRedirects(response, reverse("chats:detail", args=[chat.pk]))
        self.assertEqual(chat.messages.get().role, Message.Role.USER)
        query = chat.research_queries.get()
        self.assertEqual(query.question, chat.messages.get().content)
        self.assertEqual(query.status, ResearchQuery.Status.COMPLETED)
        self.assertIsNotNone(query.completed_at)
        self.assertEqual(query.agent_responses.filter(provider="openrouter").count(), 3)
        self.assertEqual(self.http.call_count, 3)
        self.assertFalse(FinalResponse.objects.exists())

    def test_history_isolated_ordered_and_escaped(self):
        old = save_question("First topic").chat
        newer = save_question("Other topic").chat
        save_question("<script>alert('unsafe')</script>", old)
        response = self.client.get(reverse("chats:detail", args=[old.pk]))
        self.assertEqual(list(response.context["chats"]), [old, newer])
        self.assertEqual([m.content for m in response.context["chat_messages"]], ["First topic", "<script>alert('unsafe')</script>"])
        self.assertContains(response, "&lt;script&gt;")
        self.assertNotContains(response, "<script>alert")
        old.refresh_from_db()
        self.assertEqual(old.title, "First topic")

    def test_invalid_input_does_not_write_records(self):
        for question in ["", "   ", "a" * 20001]:
            with self.subTest(length=len(question)):
                response = self.client.post(reverse("chats:submit"), {"question": question})
                self.assertEqual(response.status_code, 400)
        self.assertFalse(Chat.objects.exists())
        self.assertFalse(Message.objects.exists())
        self.assertFalse(ResearchQuery.objects.exists())

    def test_unknown_chat_returns_404(self):
        self.assertEqual(self.client.get(reverse("chats:detail", args=[999])).status_code, 404)
        self.assertEqual(self.client.post(reverse("chats:submit_to_chat", args=[999]), {"question": "Hello"}).status_code, 404)

    def test_csrf_enforced(self):
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.post(reverse("chats:new")).status_code, 403)
        self.assertEqual(client.post(reverse("chats:submit"), {"question": "Hello"}).status_code, 403)
        client.get(reverse("chats:dashboard"))
        token = client.cookies["csrftoken"].value
        self.assertEqual(client.post(reverse("chats:new"), {"csrfmiddlewaretoken": token}).status_code, 302)

    def test_failure_rolls_back_all_question_records(self):
        with patch("chats.services.ResearchQuery.objects.create", side_effect=RuntimeError("Save failed")):
            with self.assertRaises(RuntimeError):
                save_question("Question")
        self.assertFalse(Chat.objects.exists())
        self.assertFalse(Message.objects.exists())

    def test_all_models_registered_in_admin(self):
        for model in [Chat, Message, ResearchQuery, AgentResponse, Citation, FinalResponse]:
            self.assertTrue(admin.site.is_registered(model))

    def test_final_response_unique_and_citation_survives_agent_deletion(self):
        chat = save_question("Question").chat
        query = chat.research_queries.get()
        agent = AgentResponse.objects.create(research_query=query, provider="Example", model_name="example", raw_response="Evidence")
        citation = Citation.objects.create(research_query=query, agent_response=agent, title="Paper", url="https://example.com/paper", source_name="Example")
        FinalResponse.objects.create(research_query=query, answer="Answer")
        with self.assertRaises(IntegrityError), transaction.atomic():
            FinalResponse.objects.create(research_query=query, answer="Duplicate")
        agent.delete()
        citation.refresh_from_db()
        self.assertIsNone(citation.agent_response)
        chat.delete()
        self.assertFalse(Citation.objects.exists())
        self.assertFalse(FinalResponse.objects.exists())

    def test_dashboard_displays_agent_cards_and_preserves_history(self):
        first = self.client.post(reverse("chats:submit"), {"question": "First question"})
        chat = Chat.objects.get()
        second = self.client.post(reverse("chats:submit_to_chat", args=[chat.pk]), {"question": "Second question"}, follow=True)
        self.assertEqual(first.status_code, 302)
        for text in ["Agent Research", *[m.get("display_label", m["label"]) for m in settings.OPENROUTER_MODELS], "A research answer", "Key findings", "0/100", "First question", "Second question"]:
            self.assertContains(second, text)
        self.assertEqual(chat.research_queries.count(), 2)
        self.assertEqual(chat.messages.count(), 2)
        self.assertEqual(AgentResponse.objects.filter(provider="openrouter").count(), 6)

    @override_settings(OPENROUTER_API_KEY="")
    def test_missing_key_renders_safe_errors_without_network(self):
        response = self.client.post(reverse("chats:submit"), {"question": "Question"}, follow=True)
        self.assertContains(response, "OpenRouter is not configured")
        self.assertEqual(ResearchQuery.objects.get().status, ResearchQuery.Status.FAILED)
        self.assertEqual(AgentResponse.objects.filter(provider="openrouter").count(), 3)
        self.http.assert_not_called()
