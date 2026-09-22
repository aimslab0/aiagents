import json
from unittest.mock import Mock, patch

import requests
from django.test import SimpleTestCase, override_settings

from .exceptions import AgentError
from .openrouter import API_URL, OpenRouterClient
from .schemas import SYSTEM_PROMPT


def research_answer(**updates):
    answer = {
        "answer": "Sleep supports memory; causality depends on study design.",
        "key_findings": ["Sleep is associated with memory consolidation."],
        "evidence": ["Experimental evidence has limitations."],
        "citations": [{"title": "Example source", "url": "https://example.com/paper", "source_name": "Example"}],
        "confidence": 72,
    }
    answer.update(updates)
    return answer


def api_response(content=None, *, status=200, body=None, finish_reason="stop"):
    if body is None:
        body = {"id": "example-completion", "choices": [{
            "message": {"content": json.dumps(research_answer()) if content is None else content},
            "finish_reason": finish_reason,
        }]}
    return Mock(status_code=status, text=json.dumps(body))


@override_settings(OPENROUTER_API_KEY="test-openrouter-secret")
class OpenRouterTests(SimpleTestCase):
    def setUp(self):
        self.client = OpenRouterClient()
        mocked_http = patch("agents.openrouter.requests.post")
        self.http = mocked_http.start()
        self.addCleanup(mocked_http.stop)

    def test_success_and_shared_request_contract(self):
        response = api_response()
        self.http.return_value = response
        result = self.client.research("Does sleep affect memory?", "openai/gpt-5.4")
        self.assertEqual(result["provider"], "openrouter")
        self.assertEqual(result["model"], "openai/gpt-5.4")
        self.assertEqual(result["confidence"], 72)
        self.assertEqual(result["answer"], research_answer()["answer"])
        self.assertEqual(result["citations"], research_answer()["citations"])
        self.assertIn("choices", result["raw_response"])
        self.assertIsNone(result["error"])
        args, kwargs = self.http.call_args
        self.assertEqual(args, (API_URL,))
        self.assertEqual(kwargs["json"]["messages"], [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "Does sleep affect memory?"},
        ])
        self.assertEqual(kwargs["json"]["response_format"]["type"], "json_schema")
        self.assertTrue(kwargs["json"]["provider"]["require_parameters"])
        self.assertFalse(kwargs["allow_redirects"])
        self.assertEqual(kwargs["timeout"], (5, 120))
        response.close.assert_called_once()

    def test_malformed_model_json_retains_raw(self):
        for content in ["not JSON", "```json\n{}\n```", "[]", "null", '{"answer": "incomplete"}']:
            with self.subTest(content=content):
                self.http.return_value = api_response(content)
                with self.assertRaises(AgentError) as error:
                    self.client.research("Question", "model")
                self.assertEqual(error.exception.code, "malformed_response")
                self.assertIn("choices", error.exception.raw_response)

    def test_schema_validation_rejects_bad_fields(self):
        cases = [
            {"answer": " "}, {"key_findings": "not a list"}, {"evidence": [123]},
            {"citations": [{"title": "missing fields"}]},
            {"confidence": 101}, {"confidence": -1}, {"confidence": True},
            {"confidence": "80"}, {"confidence": float("nan")}, {"confidence": float("inf")},
        ]
        for fields in cases:
            with self.subTest(fields=fields):
                self.http.return_value = api_response(json.dumps(research_answer(**fields)))
                with self.assertRaises(AgentError) as error:
                    self.client.research("Question", "model")
                self.assertEqual(error.exception.code, "malformed_response")

    def test_nullable_and_zero_confidence(self):
        for confidence in [None, 0, 100]:
            self.http.return_value = api_response(json.dumps(research_answer(confidence=confidence)))
            self.assertEqual(self.client.research("Question", "model")["confidence"], confidence)

    def test_unsafe_citation_url_is_not_linked(self):
        for url in ["javascript:alert(1)", "data:text/html,unsafe", "//example.com", ""]:
            self.http.return_value = api_response(json.dumps(research_answer(citations=[{"title": "Source", "url": url, "source_name": "Journal"}])))
            result = self.client.research("Question", "model")
            self.assertEqual(result["citations"][0]["url"], "")
            self.assertEqual(result["citations"][0]["title"], "Source")

    def test_api_failures_use_safe_errors_and_redact_raw(self):
        for status, expected in [(401, "authentication"), (402, "credits"), (429, "rate_limit"), (503, "api_error"), (504, "timeout"), (302, "api_error")]:
            with self.subTest(status=status):
                self.http.return_value = api_response(status=status, body={"error": {"message": "Echo test-openrouter-secret", "code": status}})
                with self.assertRaises(AgentError) as error:
                    self.client.research("Question", "model")
                self.assertEqual(error.exception.code, expected)
                self.assertNotIn("test-openrouter-secret", str(error.exception))
                self.assertNotIn("test-openrouter-secret", json.dumps(error.exception.raw_response))

    def test_embedded_api_error_in_http_200(self):
        self.http.return_value = api_response(body={"error": {"code": 429, "message": "upstream private details"}})
        with self.assertRaises(AgentError) as error:
            self.client.research("Question", "model")
        self.assertEqual(error.exception.code, "rate_limit")
        self.assertNotIn("private details", str(error.exception))

    def test_timeout_and_connection_errors_do_not_expose_exception_text(self):
        for exception, code in [(requests.Timeout("test-openrouter-secret"), "timeout"), (requests.ConnectionError("test-openrouter-secret"), "connection")]:
            self.http.side_effect = exception
            with self.assertRaises(AgentError) as error:
                self.client.research("Question", "model")
            self.assertEqual(error.exception.code, code)
            self.assertNotIn("test-openrouter-secret", str(error.exception))
            self.assertEqual(error.exception.raw_response, {})

    def test_bad_transport_json_and_envelopes(self):
        for body in ["<html>test-openrouter-secret</html>", "[]", '{"choices": []}', '{"choices": [{"message": {"content": null}}]}', "NaN", '{"usage": 1e999}']:
            self.http.return_value = Mock(status_code=200, text=body)
            with self.assertRaises(AgentError) as error:
                self.client.research("Question", "model")
            self.assertEqual(error.exception.code, "malformed_response")
            self.assertNotIn("test-openrouter-secret", json.dumps(error.exception.raw_response))

    def test_truncated_and_refused_results(self):
        for finish, expected in [("length", "truncated_response"), ("content_filter", "refusal"), ("error", "api_error")]:
            self.http.return_value = api_response(finish_reason=finish)
            with self.assertRaises(AgentError) as error:
                self.client.research("Question", "model")
            self.assertEqual(error.exception.code, expected)

    def test_credentials_redacted_from_model_content(self):
        self.http.return_value = api_response(json.dumps(research_answer(answer="Unexpected test-openrouter-secret echo")))
        result = self.client.research("Question", "model")
        self.assertNotIn("test-openrouter-secret", json.dumps(result))

    @override_settings(OPENROUTER_API_KEY="")
    def test_missing_key_does_not_call_api(self):
        with self.assertRaises(AgentError) as error:
            self.client.research("Question", "model")
        self.assertEqual(error.exception.code, "configuration")
        self.http.assert_not_called()
