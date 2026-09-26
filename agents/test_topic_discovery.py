import json
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from agents.exceptions import AgentError
from agents.openrouter import OpenRouterClient
from agents.schemas import RESEARCH_SCHEMA, SYSTEM_PROMPT
from agents.synthesizer import SynthesizerClient
from agents.synthesis_schemas import JUDGE_PROMPT, SYNTHESIS_SCHEMA
from agents.test_consensus import search_response
from agents.test_synthesizer import judge_response, judge_result
from agents.tests import api_response
from agents.topic_discovery import is_topic_discovery, normalize_topic_content, TOPIC_PROMPT
from chats.services import save_question
from research.services import run_research
from research_ai.model_config import configure_production, configure_models


def topic_result():
    return {"problem_area": "Health education", "topic_ideas": [{
        "title": "Health literacy and student wellbeing", "research_gap": "A local population gap needs verification.",
        "why_it_matters": "May inform student support.", "population": "Master's students",
        "main_variables": ["health literacy", "wellbeing"],
        "possible_research_question": "Is health literacy associated with student wellbeing?",
        "methodology_idea": "Cross-sectional survey; hypothesize a positive association.",
        "feasibility": "One semester; needs access and ethics review.",
        "evidence_needed": ["Recent systematic reviews", "Local population studies"],
    }], "key_findings": ["Novelty is unverified."], "uncertainties": ["Local evidence may be missing from this search."],
        "citations": [], "confidence": None}


def topic_response():
    return Mock(status_code=200, text=json.dumps({"choices": [{"message": {"content": json.dumps(topic_result())}, "finish_reason": "stop"}]}))


@override_settings(OPENROUTER_API_KEY="mock-key", PROVIDER_BUDGETS={})
class TopicPromptTests(SimpleTestCase):
    def test_topic_intent_does_not_capture_normal_research(self):
        for question in ("Suggest thesis topics in education", "Find a topic for my Master's thesis", "Identify research gaps in health education"):
            self.assertTrue(is_topic_discovery(question))
        for question in ("Does sleep improve memory?", "Compare survey and experimental methods", "Summarize evidence about exercise"):
            self.assertFalse(is_topic_discovery(question))

    @patch("requests.post", return_value=topic_response())
    def test_topic_prompt_schema_and_normalized_adapter(self, post):
        result = OpenRouterClient().research("Suggest thesis topics in health education", "test/model")
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["response_format"]["json_schema"]["name"], "topic_discovery")
        self.assertTrue(payload["response_format"]["json_schema"]["strict"])
        self.assertEqual(payload["messages"][0]["content"], TOPIC_PROMPT)
        self.assertEqual(result["topic_ideas"], topic_result()["topic_ideas"])
        for value in ("health literacy", "One semester", "Cross-sectional", "Novelty"):
            self.assertIn(value, result["answer"] + str(result["key_findings"]))

    @patch("requests.post", return_value=api_response())
    def test_normal_research_contract_unchanged(self, post):
        result = OpenRouterClient().research("Does sleep improve memory?", "test/model")
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["messages"][0]["content"], SYSTEM_PROMPT)
        self.assertEqual(payload["response_format"]["json_schema"]["schema"], RESEARCH_SCHEMA)
        self.assertNotIn("topic_ideas", result)

    def test_invalid_topic_output_fails_safely(self):
        for content in ("not JSON", json.dumps({**topic_result(), "topic_ideas": []}), json.dumps({**topic_result(), "topic_ideas": topic_result()["topic_ideas"] * 4})):
            with self.assertRaises(AgentError) as error:
                normalize_topic_content(content, "test/model", {})
            self.assertEqual(error.exception.code, "malformed_response")

    def test_topic_citations_use_existing_url_sanitization(self):
        data = topic_result()
        data["citations"] = [{"title": "Unverified", "url": "javascript:alert(1)", "source_name": "Example"}]
        self.assertEqual(normalize_topic_content(json.dumps(data), "test/model", {})["citations"][0]["url"], "")

    @patch("requests.post", return_value=judge_response())
    def test_judge_prompt_changes_only_for_discovery_and_keeps_schema(self, post):
        client = SynthesizerClient()
        client.synthesize({"question": "Suggest research topics for education"})
        payload = post.call_args.kwargs["json"]
        self.assertIn("TOPIC DISCOVERY", payload["messages"][0]["content"])
        self.assertIn("not found in current", payload["messages"][0]["content"])
        self.assertIn("at most three", payload["messages"][0]["content"])
        self.assertEqual(payload["response_format"]["json_schema"]["schema"], SYNTHESIS_SCHEMA)
        client.synthesize({"question": "Does sleep improve memory?"})
        self.assertEqual(post.call_args.kwargs["json"]["messages"][0]["content"], JUDGE_PROMPT)


class TopicPipelineTests(TestCase):
    def test_balanced_topic_pipeline_persists_and_labels_qwen(self):
        models, judge = configure_production({})
        config = configure_models({}, models, judge, [judge])
        def respond(url, **kwargs):
            payload = kwargs["json"]
            if payload["response_format"]["json_schema"]["name"] == "final_synthesis":
                context = json.loads(payload["messages"][1]["content"])
                self.assertTrue(all("Variables:" in item["answer"] for item in context["agent_findings"]))
                return judge_response(judge_result(context["allowed_source_ids"][:1]))
            return topic_response()
        with override_settings(**config, OPENROUTER_API_KEY="mock-key", CONSENSUS_API_KEY="mock-key", PROVIDER_BUDGETS={}, OPENROUTER_MAX_RETRIES=0), patch("requests.post", side_effect=respond) as post, patch("requests.get", return_value=search_response()):
            query = save_question("Suggest Master's thesis topics in health education")
            run_research(query)
            self.assertEqual(query.stage, "COMPLETED")
            self.assertEqual(post.call_count, 4)
            third = query.agent_responses.get(provider_key="claude")
            self.assertEqual(third.model_name, "qwen/qwen3.5-27b")
            self.assertEqual(third.normalized_response["label"], "Planner 3 — Qwen3.5-27B")
            self.assertEqual(third.normalized_response["topic_ideas"], topic_result()["topic_ideas"])
            self.assertEqual(query.final_response.synthesis_data["model"], "deepseek/deepseek-v4-flash")
            page = self.client.get(reverse("chats:detail", args=[query.chat_id]))
            self.assertContains(page, "Retry Planner 3 — Qwen3.5-27B")
            self.assertNotContains(page, "Retry Claude")
            self.assertContains(page, "Health literacy and student wellbeing")
