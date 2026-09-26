import json
from unittest.mock import patch

from django.test import Client, TestCase, override_settings
from django.urls import reverse

from agents.prompt_enhancer import EnhancementError
from chats.models import Chat, Message
from research.models import ResearchQuery


class PromptEnhancerEndpointTests(TestCase):
    @override_settings(DEBUG=True, OPENROUTER_API_KEY="")
    def test_missing_key_development_error(self):
        result = self.client.post(reverse("chats:enhance_prompt"), {"text": "sleep"}, content_type="application/json")
        self.assertEqual(result.status_code, 503)
        self.assertEqual(result.json()["error"], "OpenRouter API key is not configured.")

    def setUp(self):
        self.url = reverse("chats:enhance_prompt")

    @patch("chats.prompt_enhancer.enhance_prompt")
    def test_csrf_flow_and_no_research_history(self, enhance):
        enhance.return_value = {"enhanced_text": "Academic question?", "model_used": "free-model"}
        client = Client(enforce_csrf_checks=True)
        page = client.get(reverse("chats:dashboard"))
        self.assertContains(page, 'type="button" id="enhance-prompt"')
        self.assertContains(page, 'data-url="/enhance-prompt/"')
        self.assertContains(page, "js/prompt-enhancer.js")
        self.assertEqual(client.post(self.url, {"text": "sleep"}, content_type="application/json").status_code, 403)
        result = client.post(self.url, {"text": " sleep "}, content_type="application/json", HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json(), enhance.return_value)
        enhance.assert_called_once_with("sleep")
        self.assertEqual(Chat.objects.count(), 0)
        self.assertEqual(Message.objects.count(), 0)
        self.assertEqual(ResearchQuery.objects.count(), 0)

    @patch("chats.prompt_enhancer.enhance_prompt")
    def test_empty_oversized_and_invalid_input(self, enhance):
        for data in ({"text": ""}, {"text": "  "}, {"text": "x" * 2001}, {"text": 3}, [], None, {}):
            with self.subTest(data_type=type(data).__name__):
                result = self.client.post(self.url, json.dumps(data), content_type="application/json")
                self.assertEqual(result.status_code, 400)
        self.assertEqual(self.client.post(self.url, "{bad", content_type="application/json").status_code, 400)
        self.assertEqual(self.client.post(self.url, "[" * 1500 + "]" * 1500, content_type="application/json").status_code, 400)
        self.assertEqual(self.client.post(self.url, b"\xff", content_type="application/json").status_code, 400)
        self.assertEqual(self.client.post(self.url, "x" * 25000, content_type="application/json").status_code, 413)
        self.assertEqual(self.client.post(self.url, {"text": "sleep"}).status_code, 415)
        enhance.assert_not_called()

    def test_post_only(self):
        for method in ("get", "put", "delete", "head"):
            self.assertEqual(getattr(self.client, method)(self.url).status_code, 405)

    @patch("chats.prompt_enhancer.enhance_prompt", side_effect=EnhancementError())
    def test_failure_is_safe_json(self, enhance):
        result = self.client.post(self.url, {"text": "sleep"}, content_type="application/json")
        self.assertEqual(result.status_code, 503)
        self.assertIn("temporarily unavailable", result.json()["error"])
