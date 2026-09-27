from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse


class LandingTests(TestCase):
    @override_settings(RESEARCH_DIAGNOSTICS_ENABLED=True, OPENROUTER_API_KEY="private-router-key", CONSENSUS_API_KEY="private-consensus-key", SEMANTIC_SCHOLAR_API_KEY="private-scholar-key")
    def test_landing_and_settings_separate_public_copy_from_diagnostics(self):
        response = self.client.get(reverse("chats:dashboard"))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        main = html.split('<main class="main-panel"', 1)[1]
        sidebar = html.split('</aside>', 1)[0]
        for text in ("THOR", "Thoughtful Higher-Order Research Agent", "Start Research", "Find a research gap", "Develop a thesis topic", "Compare evidence"):
            self.assertIn(text, main)
        for text in ("Developer Diagnostics", "PRODUCTION MODE", "FREE TEST MODE", "Credential readiness"):
            self.assertNotIn(text, main)
        for text in ("Developer Diagnostics", "Research Configuration", "About THOR", "Research Planners", "Semantic Scholar", "Consensus", settings.SYNTHESIZER_MODEL):
            self.assertIn(text, sidebar)
        for model in response.context["configured_models"]:
            self.assertIn(model["id"], sidebar)
        for secret in ("private-router-key", "private-consensus-key", "private-scholar-key"):
            self.assertNotIn(secret, html)

    @override_settings(RESEARCH_DIAGNOSTICS_ENABLED=False)
    def test_diagnostics_setting_is_respected(self):
        response = self.client.get(reverse("chats:dashboard"))
        self.assertNotContains(response, "Developer Diagnostics")
        self.assertContains(response, "About THOR")
