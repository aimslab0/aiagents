from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from agents.test_consensus import search_response
from agents.test_synthesizer import judge_response
from agents.tests import api_response
from chats.services import save_question
from research_ai.model_config import PRODUCTION_MODEL_DEFAULTS, PRODUCTION_RESEARCH_SLOTS, VERIFIED_FREE_MODELS, configure_models, production_model, configure_production
from .services import run_research


class ProductionConfigurationTests(SimpleTestCase):
    def test_balanced_defaults_and_labels(self):
        models, judge = configure_production({})
        self.assertEqual([m["id"] for m in models], ["google/gemini-3.5-flash", "meta-llama/llama-4-scout", "qwen/qwen3.5-27b"])
        self.assertEqual(models[2]["display_label"], "Planner 3 — Qwen3.5-27B")
        self.assertEqual(judge, "deepseek/deepseek-v4-flash")

    def test_deep_preserves_previous_configuration_and_supports_overrides(self):
        models, judge = configure_production({"RESEARCH_MODE": "deep"})
        self.assertEqual([m["id"] for m in models], [PRODUCTION_MODEL_DEFAULTS[label] for label in PRODUCTION_RESEARCH_SLOTS])
        self.assertEqual(judge, "deepseek/deepseek-r1")
        models, judge = configure_production({"RESEARCH_MODE": "deep", "CLAUDE_MODEL": "custom/claude", "DEEP_SYNTHESIZER_DEEPSEEK_MODEL": "custom/judge"})
        self.assertEqual(models[2]["id"], "custom/claude")
        self.assertEqual(judge, "custom/judge")

    def test_balanced_overrides_are_independent_from_legacy_deep_settings(self):
        models, judge = configure_production({"PLANNER_MODEL_3": "custom/third", "PRIMARY_SYNTHESIZER_MODEL": "custom/judge", "CLAUDE_MODEL": "deep/claude", "SYNTHESIZER_MODEL": "deep/judge"})
        self.assertEqual(models[2]["id"], "custom/third")
        self.assertEqual(models[2]["display_label"], "custom/third")
        self.assertEqual(judge, "custom/judge")

    def test_paid_defaults_and_separate_judge(self):
        self.assertEqual(production_model({}, "GPT"), "openai/gpt-6-astra")
        self.assertEqual(production_model({}, "Claude"), "anthropic/claude-opus-5")
        self.assertEqual(production_model({}, "Gemini"), "google/gemini-3.1-pro-preview")
        self.assertEqual(production_model({}, "synthesis"), "anthropic/claude-fable-5.1")
        self.assertEqual(production_model({"SYNTHESIZER_MODEL": "custom/judge", "GPT_MODEL": "custom/agent"}, "synthesis"), "custom/judge")

    def test_canonical_environment_names_win_and_legacy_names_work(self):
        self.assertEqual(production_model({"GPT_MODEL": "custom/new", "OPENROUTER_GPT_MODEL": "custom/old"}, "GPT"), "custom/new")
        self.assertEqual(production_model({"OPENROUTER_CLAUDE_MODEL": "custom/legacy"}, "Claude"), "custom/legacy")

    def test_claude_agent_and_judge_are_independent(self):
        environment = {"SYNTHESIZER_MODEL": "custom/judge"}
        self.assertEqual(production_model(environment, "Claude"), "anthropic/claude-opus-5")
        self.assertEqual(production_model(environment, "synthesis"), "custom/judge")
        environment = {"CLAUDE_MODEL": "custom/research"}
        self.assertEqual(production_model(environment, "Claude"), "custom/research")
        self.assertEqual(production_model(environment, "synthesis"), "anthropic/claude-fable-5.1")

    def test_both_modes_preserve_previous_free_choices(self):
        paid, judge = configure_production({})
        self.assertEqual([m["label"] for m in paid], ["GPT", "Gemini", "Claude"])
        free = configure_models({"OPENROUTER_FREE_TEST_MODE": "True"}, paid, judge, [judge])
        self.assertEqual([m["id"] for m in free["OPENROUTER_MODELS"]], list(VERIFIED_FREE_MODELS))
        self.assertEqual([m["label"] for m in free["OPENROUTER_MODELS"]], ["GPT", "Claude", "Gemini"])
        self.assertEqual(free["SYNTHESIZER_MODEL"], VERIFIED_FREE_MODELS[0])
        production = configure_models({"OPENROUTER_FREE_TEST_MODE": "False"}, paid, judge, [judge])
        self.assertEqual(production["OPENROUTER_MODELS"], paid)
        self.assertEqual(production["SYNTHESIZER_MODEL"], judge)
        self.assertNotIn("max_price", production["OPENROUTER_PROVIDER_OPTIONS"])


class ProductionWorkflowTests(TestCase):
    def test_mocked_paid_pipeline_budgets_and_diagnostics(self):
        paid, judge = configure_production({})
        config = configure_models({"OPENROUTER_FREE_TEST_MODE": "False"}, paid, judge, [judge])
        def respond(url, **kwargs):
            return judge_response() if kwargs["json"]["response_format"]["json_schema"]["name"] == "final_synthesis" else api_response()
        with override_settings(**config, OPENROUTER_API_KEY="mock-key", CONSENSUS_API_KEY="mock-key", PROVIDER_BUDGETS={},
                               OPENROUTER_READ_TIMEOUT=120, OPENROUTER_MAX_TOKENS=4096, SYNTHESIZER_TIMEOUT=180,
                               SYNTHESIZER_MAX_TOKENS=6000, OPENROUTER_MAX_RETRIES=0, CONSENSUS_MAX_RETRIES=0, RESEARCH_DIAGNOSTICS_ENABLED=True), patch("requests.post", side_effect=respond) as post, patch("requests.get", return_value=search_response()):
            query = save_question("Does sleep improve memory?")
            run_research(query)
            self.assertEqual(query.stage, "COMPLETED")
            self.assertEqual([call.kwargs["json"]["model"] for call in post.call_args_list], [m["id"] for m in paid] + [judge])
            self.assertEqual([call.kwargs["json"]["max_tokens"] for call in post.call_args_list], [4096, 4096, 4096, 6000])
            self.assertEqual([call.kwargs["timeout"][1] for call in post.call_args_list], [120, 120, 120, 180])
            for call in post.call_args_list:
                self.assertTrue(call.kwargs["json"]["provider"]["require_parameters"])
                self.assertEqual(call.kwargs["json"]["response_format"]["type"], "json_schema")
                self.assertTrue(call.kwargs["json"]["response_format"]["json_schema"]["strict"])
            page = self.client.get(reverse("chats:detail", args=[query.chat_id]))
            for text in ["PRODUCTION MODE", "Latency ms", "Input / output / total tokens", "Cost USD", *[m["id"] for m in paid]]:
                self.assertContains(page, text)
            self.assertNotContains(page, "FREE TEST MODE")
            self.assertContains(page, f"Synthesizer: {judge}")
