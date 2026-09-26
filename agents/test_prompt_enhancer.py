import json
from unittest.mock import Mock, patch

import requests
from django.conf import settings
from django.test import SimpleTestCase, override_settings

from .prompt_enhancer import SYSTEM_PROMPT, EnhancementError, enhance_prompt


def response(status=200, content='```text\n"How does sleep influence academic attainment?"\n```'):
    return Mock(status_code=status, text=json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": content}}]}))


@override_settings(OPENROUTER_API_KEY="test-key-only")
class PromptEnhancerTests(SimpleTestCase):
    @patch("agents.prompt_enhancer.requests.post")
    def test_gemini_success_and_cleanup(self, post):
        post.return_value = response()
        result = enhance_prompt("sleep and grades")
        self.assertEqual(result, {"enhanced_text": "How does sleep influence academic attainment?", "model_used": settings.PROMPT_ENHANCER_MODEL})
        post.assert_called_once()
        options = post.call_args.kwargs
        self.assertEqual(options["json"]["model"], settings.PROMPT_ENHANCER_MODEL)
        self.assertEqual(options["json"]["messages"][0]["content"], SYSTEM_PROMPT)
        self.assertEqual(options["json"]["max_tokens"], 256)
        self.assertEqual(options["json"]["provider"], {"allow_fallbacks": False})
        self.assertNotIn("response_format", options["json"])
        self.assertFalse(options["allow_redirects"])
        self.assertEqual(options["timeout"], (5, 20))
        post.return_value.close.assert_called_once()

    @patch("agents.prompt_enhancer.requests.post")
    def test_api_and_transport_failures_make_one_request(self, post):
        for failure in (response(404), response(429), response(503), requests.Timeout("test-key-only"), requests.ConnectionError()):
            with self.subTest(failure=type(failure).__name__):
                post.reset_mock()
                post.side_effect = [failure]
                with self.assertRaises(EnhancementError) as caught:
                    enhance_prompt("sleep")
                self.assertNotIn("test-key-only", str(caught.exception))
                post.assert_called_once()

    @patch("agents.prompt_enhancer.requests.post")
    def test_authentication_and_malformed_output_do_not_retry(self, post):
        for failed in (response(401), response(402), response(content=""), Mock(status_code=200, text="not JSON"), Mock(status_code=200, text="null")):
            with self.subTest(failed=failed):
                post.reset_mock()
                post.return_value = failed
                with self.assertRaises(EnhancementError):
                    enhance_prompt("sleep")
                post.assert_called_once()

    @override_settings(OPENROUTER_API_KEY="")
    @patch("agents.prompt_enhancer.requests.post")
    def test_missing_key_never_calls_provider(self, post):
        with self.assertRaises(EnhancementError):
            enhance_prompt("sleep")
        post.assert_not_called()

    @patch("agents.prompt_enhancer.requests.post")
    def test_alternate_text_and_prefixes(self, post):
        for choice in (
            {"message": {"content": [{"type": "text", "text": 'Enhanced prompt: "Academic question?"'}]}},
            {"text": "Rewritten question: Academic question?"},
            {"message": {"content": None, "output_text": "Academic question?", "reasoning": "private reasoning"}},
        ):
            post.return_value = Mock(status_code=200, text=json.dumps({"choices": [choice]}))
            self.assertEqual(enhance_prompt("sleep")["enhanced_text"], "Academic question?")

    @patch("agents.prompt_enhancer.requests.post")
    def test_reasoning_only_not_returned(self, post):
        post.return_value = Mock(status_code=200, text=json.dumps({"choices": [{"message": {"content": None, "reasoning": "private reasoning"}}]}))
        with self.assertRaises(EnhancementError) as caught:
            enhance_prompt("sleep")
        self.assertEqual(caught.exception.category, "reasoning_without_final_text")

    @patch("agents.prompt_enhancer.requests.post")
    def test_sanitized_diagnostics_and_embedded_error(self, post):
        post.return_value = Mock(status_code=200, text=json.dumps({"error": {"code": "503", "message": "Unavailable test-key-only Bearer another-secret\nnew line"}}))
        with self.assertLogs("agents.prompt_enhancer", level="INFO") as logs, self.assertRaises(EnhancementError):
            enhance_prompt("sleep")
        output = " ".join(logs.output)
        self.assertNotIn("test-key-only", output)
        self.assertNotIn("another-secret", output)
        for expected in ("http_status=200", "provider_error", "final_failure"):
            self.assertIn(expected, output)

    @patch("agents.prompt_enhancer.requests.post")
    def test_non_json_provider_failure(self, post):
        post.return_value = Mock(status_code=503, text="Unavailable")
        with self.assertRaises(EnhancementError) as caught:
            enhance_prompt("sleep")
        self.assertEqual(caught.exception.category, "provider_error")
        post.assert_called_once()

    @override_settings(PROMPT_ENHANCER_MODEL="configured-model")
    @patch("agents.prompt_enhancer.requests.post")
    def test_model_setting_is_used(self, post):
        post.return_value = response()
        self.assertEqual(enhance_prompt("sleep")["model_used"], "configured-model")
        self.assertEqual(post.call_args.kwargs["json"]["model"], "configured-model")
