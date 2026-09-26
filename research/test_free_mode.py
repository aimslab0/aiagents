import importlib.util
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from agents.test_consensus import search_response
from agents.test_synthesizer import judge_response
from agents.tests import api_response
from chats.services import save_question
from research_ai.model_config import configure_models, VERIFIED_FREE_MODELS
from .services import run_research, retry_research


def configured(free=True):
    return configure_models({"OPENROUTER_FREE_TEST_MODE": str(free)}, settings.OPENROUTER_PRODUCTION_MODELS,
                            settings.PRODUCTION_SYNTHESIZER_MODEL, settings.PRODUCTION_SYNTHESIZER_MODELS)


class FreeConfigurationTests(SimpleTestCase):
    def test_free_selects_three_distinct_catalog_verified_ids(self):
        result = configured()
        self.assertEqual([m["id"] for m in result["OPENROUTER_MODELS"]], list(VERIFIED_FREE_MODELS))
        self.assertEqual(len(set(m["id"] for m in result["OPENROUTER_MODELS"])), 3)
        self.assertEqual([m["display_label"] for m in result["OPENROUTER_MODELS"]], ["Agent 1", "Agent 2", "Agent 3"])
        self.assertEqual([m["label"] for m in result["OPENROUTER_MODELS"]], ["GPT", "Claude", "Gemini"])

    def test_production_configuration_is_unchanged(self):
        before = [dict(m) for m in settings.OPENROUTER_PRODUCTION_MODELS]
        configured()
        result = configured(False)
        self.assertEqual(result["OPENROUTER_MODELS"], before)
        self.assertEqual(result["SYNTHESIZER_MODEL"], settings.PRODUCTION_SYNTHESIZER_MODEL)
        self.assertEqual(result["SYNTHESIZER_MODELS"], settings.PRODUCTION_SYNTHESIZER_MODELS)
        self.assertEqual(result["OPENROUTER_PROVIDER_OPTIONS"], {"require_parameters": True})

    def test_free_judge_switch_excludes_paid_alternate_judges(self):
        result = configure_models({"OPENROUTER_FREE_TEST_MODE": "True"}, settings.OPENROUTER_PRODUCTION_MODELS,
                                  "custom/paid-judge", ["custom/paid-judge", "another/paid-judge"])
        self.assertEqual(result["SYNTHESIZER_MODEL"], VERIFIED_FREE_MODELS[0])
        self.assertEqual(result["SYNTHESIZER_MODELS"], [VERIFIED_FREE_MODELS[0]])
        self.assertEqual(result["OPENROUTER_PROVIDER_OPTIONS"]["max_price"], {"prompt": 0, "completion": 0, "request": 0})

    def test_unverified_or_paid_override_fails_closed(self):
        for key in ("FREE_TEST_MODEL_1", "FREE_TEST_SYNTHESIZER_MODEL"):
            for model in ("openai/gpt-5.4", "unknown/provider:free"):
                with self.subTest(key=key, model=model), self.assertRaises(ImproperlyConfigured):
                    configure_models({"OPENROUTER_FREE_TEST_MODE": "True", key: model}, settings.OPENROUTER_PRODUCTION_MODELS,
                                     settings.PRODUCTION_SYNTHESIZER_MODEL, settings.PRODUCTION_SYNTHESIZER_MODELS)

    def test_explicit_free_overrides_and_blank_defaults(self):
        result = configure_models({"OPENROUTER_FREE_TEST_MODE": "True", "FREE_TEST_MODEL_1": "",
                                   "FREE_TEST_SYNTHESIZER_MODEL": VERIFIED_FREE_MODELS[2]},
                                  settings.OPENROUTER_PRODUCTION_MODELS, "paid/judge", ["paid/judge"])
        self.assertEqual(result["OPENROUTER_MODELS"][0]["id"], VERIFIED_FREE_MODELS[0])
        self.assertEqual(result["SYNTHESIZER_MODEL"], VERIFIED_FREE_MODELS[2])

    def test_real_settings_loading_restores_original_defaults(self):
        import os
        for flag in ("True", "False"):
            with self.subTest(flag=flag), patch.dict(os.environ, {"SECRET_KEY": "test-secret", "OPENROUTER_FREE_TEST_MODE": flag}, clear=True), patch("dotenv.load_dotenv"):
                spec = importlib.util.spec_from_file_location("research_ai._test_settings", Path(settings.BASE_DIR) / "research_ai" / "settings.py")
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                self.assertEqual([m["id"] for m in module.OPENROUTER_PRODUCTION_MODELS], ["google/gemini-3.5-flash", "meta-llama/llama-4-scout", "qwen/qwen3.5-27b"])
                expected = list(VERIFIED_FREE_MODELS) if flag == "True" else [m["id"] for m in module.OPENROUTER_PRODUCTION_MODELS]
                self.assertEqual([m["id"] for m in module.OPENROUTER_MODELS], expected)
                self.assertEqual(module.SYNTHESIZER_MODEL, VERIFIED_FREE_MODELS[0] if flag == "True" else "deepseek/deepseek-v4-flash")


@override_settings(OPENROUTER_API_KEY="free-test-key", CONSENSUS_API_KEY="consensus-test-key",
                   OPENROUTER_MAX_RETRIES=0, CONSENSUS_MAX_RETRIES=0, RESEARCH_DIAGNOSTICS_ENABLED=True,
                   PROVIDER_BUDGETS={})
class FreeWorkflowTests(TestCase):
    def test_full_mocked_pipeline_and_retry_use_only_free_ids(self):
        def respond(url, **kwargs):
            return judge_response() if kwargs["json"]["response_format"]["json_schema"]["name"] == "final_synthesis" else api_response()
        with override_settings(**configured()), patch("requests.post", side_effect=respond) as post, patch("requests.get", return_value=search_response()) as get:
            query = save_question("How does sleep affect memory?")
            run_research(query)
            self.assertEqual(query.stage, "COMPLETED")
            self.assertEqual([call.kwargs["json"]["model"] for call in post.call_args_list], [*VERIFIED_FREE_MODELS, VERIFIED_FREE_MODELS[0]])
            for call in post.call_args_list:
                self.assertEqual(call.kwargs["json"]["provider"]["max_price"]["prompt"], 0)
                self.assertEqual(call.kwargs["json"]["response_format"]["type"], "json_schema")
            get.assert_called_once()
            rows = list(query.agent_responses.filter(provider="openrouter").order_by("pk"))
            self.assertEqual([r.provider_key for r in rows], ["gpt", "claude", "gemini"])
            self.assertEqual([r.normalized_response["label"] for r in rows], ["Agent 1", "Agent 2", "Agent 3"])
            page = self.client.get(reverse("chats:detail", args=[query.chat_id]))
            self.assertContains(page, "FREE TEST MODE")
            self.assertContains(page, "Retry Agent 1")
            self.assertNotContains(page, "Retry GPT")
            for model_id in VERIFIED_FREE_MODELS:
                self.assertContains(page, model_id)
            retry_research(query, "gpt")
            self.assertEqual(post.call_args.kwargs["json"]["model"], VERIFIED_FREE_MODELS[0])
            with self.assertRaises(ValueError):
                retry_research(query, "synthesis", model_id="openai/gpt-5.4")

    def test_production_badge_does_not_claim_free_mode(self):
        with override_settings(**configured(False)):
            page = self.client.get(reverse("chats:dashboard"))
            self.assertNotContains(page, "FREE TEST MODE")
