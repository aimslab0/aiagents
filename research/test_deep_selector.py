import json
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse

from agents.test_consensus import search_response
from agents.test_synthesizer import judge_response, judge_result
from agents.tests import api_response
from chats.services import save_question
from research_ai.model_config import configure_models, configure_production, deep_judges
from .configuration import select_configuration
from .models import ResearchQuery
from .recovery import recovery_plan


@override_settings(OPENROUTER_API_KEY="mock-key", CONSENSUS_API_KEY="mock-key", PROVIDER_BUDGETS={},
                   OPENROUTER_MAX_RETRIES=0, CONSENSUS_MAX_RETRIES=0, OPENROUTER_FREE_TEST_MODE=False,
                   RESEARCH_MODE="balanced", DEEP_DEFAULT_SYNTHESIZER="gpt")
class DeepSelectorTests(TestCase):
    def setUp(self):
        def respond(url, **kwargs):
            payload = kwargs["json"]
            if payload["response_format"]["json_schema"]["name"] == "final_synthesis":
                context = json.loads(payload["messages"][1]["content"])
                return judge_response(judge_result(context["allowed_source_ids"][:1]))
            return api_response()
        post = patch("requests.post", side_effect=respond)
        get = patch("requests.get", return_value=search_response())
        self.post, self.get = post.start(), get.start()
        self.addCleanup(post.stop)
        self.addCleanup(get.stop)

    def submit(self, **extra):
        return self.client.post(reverse("chats:submit"), {"question": "Does sleep improve memory?", "research_mode": "deep", **extra})

    def test_balanced_hides_selector_and_defaults_to_grok(self):
        page = self.client.get(reverse("chats:dashboard"))
        self.assertContains(page, 'id="deep-synthesizer-control" hidden')
        result = self.client.post(reverse("chats:submit"), {"question": "Does sleep improve memory?", "research_mode": "balanced", "deep_synthesizer": "claude"})
        self.assertEqual(result.status_code, 302)
        query = ResearchQuery.objects.get()
        self.assertEqual(query.final_response.synthesis_data["model"], "x-ai/grok-4.20")
        self.assertEqual(query.agent_responses.get(provider_key="claude").model_name, "qwen/qwen3.8-max-0902")

    @override_settings(RESEARCH_MODE="deep")
    def test_deep_shows_selector_with_gpt_default(self):
        page = self.client.get(reverse("chats:dashboard"))
        self.assertNotContains(page, 'id="deep-synthesizer-control" hidden')
        self.assertContains(page, 'value="gpt" selected')
        for option in ("GPT-6 Astra", "Claude Fable 5.1", "Grok 4.20"):
            self.assertContains(page, option)

    def test_default_judge_and_selection_persist(self):
        self.assertEqual(self.submit().status_code, 302)
        query = ResearchQuery.objects.get()
        self.assertEqual(query.execution_data["selection"]["judge_key"], "gpt")
        self.assertEqual(query.execution_data["judge_model"], "openai/gpt-6-astra")
        self.assertEqual(query.final_response.active_attempt.model_name, "openai/gpt-6-astra")
        expected_models = [m["id"] for m in settings.RESEARCH_PROFILES["deep"][0]]
        self.assertEqual([call.kwargs["json"]["model"] for call in self.post.call_args_list[:3]], expected_models)
        context = json.loads(self.post.call_args_list[-1].kwargs["json"]["messages"][1]["content"])
        self.assertEqual(context["context_limits"]["missing_agent_models"], [])

    def test_all_judge_keys_map_to_central_models(self):
        for option in settings.DEEP_SYNTHESIZER_OPTIONS:
            with self.subTest(key=option["key"]):
                self.submit(deep_synthesizer=option["key"])
                query = ResearchQuery.objects.latest("pk")
                self.assertEqual(query.final_response.active_attempt.model_name, option["id"])
                self.assertEqual(query.execution_data["selection"]["judge_key"], option["key"])

    def test_invalid_selection_rejected_before_network_or_save(self):
        self.assertEqual(self.submit(deep_synthesizer="arbitrary/model").status_code, 400)
        self.assertFalse(ResearchQuery.objects.exists())
        self.post.assert_not_called()
        self.get.assert_not_called()

    def test_economy_not_invented_and_unknown_mode_rejected(self):
        # No Economy profile exists in this repository; do not silently map it to paid Deep.
        self.assertEqual(self.submit(research_mode="economy").status_code, 400)
        self.post.assert_not_called()

    def test_resynthesis_only_calls_judge_and_preserves_history(self):
        self.submit()
        query = ResearchQuery.objects.get()
        old_active = query.final_response.active_attempt_id
        response_ids = list(query.agent_responses.values_list("pk", flat=True))
        self.post.reset_mock()
        self.get.reset_mock()
        page = self.client.post(reverse("chats:retry", args=[query.pk]), {"target": "synthesis", "deep_synthesizer": "claude", "action_key": uuid4()}, follow=True)
        self.assertEqual(self.post.call_count, 1)
        self.get.assert_not_called()
        self.assertEqual(self.post.call_args.kwargs["json"]["model"], "anthropic/claude-fable-5.1")
        query.refresh_from_db()
        self.assertEqual(list(query.agent_responses.values_list("pk", flat=True)), response_ids)
        self.assertEqual(query.synthesis_attempts.count(), 2)
        self.assertTrue(query.synthesis_attempts.filter(pk=old_active).exists())
        self.assertNotEqual(query.final_response.active_attempt_id, old_active)
        for value in ("Active / latest accepted", "Latency:", "Estimated cost:", "Confidence:", "Claude Fable 5.1"):
            self.assertContains(page, value)
        self.assertEqual(query.execution_data["selection"]["mode"], "deep")
        self.assertEqual(recovery_plan(query)["judge_model"], "anthropic/claude-fable-5.1")

    def test_deep_rejects_browser_model_ids_and_invalid_keys(self):
        self.submit()
        query = ResearchQuery.objects.get()
        self.post.reset_mock()
        for extra in ({"deep_synthesizer": "other/model"}, {"judge_model": "anthropic/claude-fable-5.1"}):
            self.assertEqual(self.client.post(reverse("chats:retry", args=[query.pk]), {"target": "synthesis", **extra}).status_code, 400)
        self.post.assert_not_called()

    def test_free_deep_never_calls_premium_models(self):
        models, judge = configure_production({})
        config = configure_models({"OPENROUTER_FREE_TEST_MODE": "True"}, models, judge, [judge])
        with override_settings(**config):
            self.submit(deep_synthesizer="claude")
            query = ResearchQuery.objects.get()
            self.assertTrue(query.execution_data["selection"]["free_test"])
            self.assertEqual(query.final_response.active_attempt.model_name, config["SYNTHESIZER_MODEL"])
            self.assertTrue(all(call.kwargs["json"]["model"].endswith(":free") for call in self.post.call_args_list))
            self.assertEqual(self.client.post(reverse("chats:retry", args=[query.pk]), {"target": "synthesis", "deep_synthesizer": "gpt"}).status_code, 400)

    def test_duplicate_submission_cannot_change_judge(self):
        key = uuid4()
        selection = select_configuration("deep", "gpt")
        query = save_question("Same question", submission_key=key, selection=selection)
        with self.assertRaises(ValueError):
            save_question("Same question", submission_key=key, selection=select_configuration("deep", "claude"))
        self.assertEqual(ResearchQuery.objects.get(pk=query.pk).execution_data["selection"], selection)

    def test_configurable_deep_model_defaults(self):
        choices, default = deep_judges({"DEEP_DEFAULT_SYNTHESIZER": "grok", "DEEP_SYNTHESIZER_GROK_MODEL": "configured/grok"})
        self.assertEqual(default, "grok")
        self.assertEqual(next(o["id"] for o in choices if o["key"] == "grok"), "configured/grok")
