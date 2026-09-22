import json
from unittest.mock import patch

import requests
from django.test import TestCase, override_settings
from django.urls import reverse

from agents.consensus_schemas import normalize_search
from agents.test_consensus import paper_fixture, search_fixture, search_response
from agents.tests import api_response
from chats.services import save_question

from .evidence import persist_evidence
from .models import AgentResponse, Citation, FinalResponse, ResearchQuery
from .services import run_research


@override_settings(OPENROUTER_API_KEY="openrouter-test-secret", CONSENSUS_API_KEY="consensus-test-secret",
                   CONSENSUS_YEAR_MIN=None, CONSENSUS_STUDY_TYPES=[])
class AcademicEvidenceTests(TestCase):
    def setUp(self):
        synthesis = patch("research.services.synthesize_research")
        synthesis.start()
        self.addCleanup(synthesis.stop)
        self.query = save_question("Does sleep affect memory?")
        post = patch("agents.openrouter.requests.post", return_value=api_response())
        get = patch("agents.consensus.requests.get", return_value=search_response())
        self.post, self.get = post.start(), get.start()
        self.addCleanup(post.stop)
        self.addCleanup(get.stop)

    def test_order_and_status_until_consensus_finishes(self):
        def evidence(*args, **kwargs):
            self.query.refresh_from_db()
            self.assertEqual(self.query.status, ResearchQuery.Status.PROCESSING)
            self.assertIsNone(self.query.completed_at)
            self.assertEqual(self.query.agent_responses.filter(provider="openrouter").count(), 3)
            self.assertEqual(kwargs["params"]["query"], self.query.question)
            return search_response()

        self.get.side_effect = evidence
        run_research(self.query)
        self.assertEqual(self.query.status, ResearchQuery.Status.COMPLETED)
        self.assertIsNotNone(self.query.completed_at)
        self.assertEqual(self.query.agent_responses.count(), 4)
        self.assertFalse(FinalResponse.objects.exists())

    def test_citations_and_metadata_persist(self):
        run_research(self.query)
        agent = self.query.agent_responses.get(provider="consensus")
        citation = self.query.citations.get()
        self.assertEqual(citation.agent_response, agent)
        self.assertEqual(citation.title, paper_fixture()["title"])
        self.assertEqual(citation.source_name, "Example Journal")
        self.assertEqual(citation.published_date.isoformat(), "2025-03-12")
        for key, expected in {"year": 2025, "doi": "10.1234/example.sleep", "authors": ["A. Example", "B. Example"], "citation_count": 0, "sample_size": 120, "relevance_score": 0.91, "study_type": "systematic review"}.items():
            self.assertEqual(citation.metadata[key], expected)
        self.assertEqual(citation.metadata["api_metadata"]["sjr_best_quartile"], 1)
        self.assertFalse(citation.metadata["api_metadata"]["is_preprint"])
        self.assertEqual(json.loads(agent.raw_response), agent.normalized_response["raw_response"])
        self.assertIsNone(agent.confidence)

    def test_consensus_failure_preserves_openrouter_success(self):
        self.get.side_effect = requests.Timeout("consensus-test-secret")
        run_research(self.query)
        self.assertEqual(self.query.status, ResearchQuery.Status.COMPLETED)
        self.assertEqual(self.query.agent_responses.filter(provider="openrouter").count(), 3)
        self.assertEqual(self.query.agent_responses.get(provider="consensus").normalized_response["error"]["code"], "timeout")
        self.assertFalse(Citation.objects.exists())
        page = self.client.get(reverse("chats:detail", args=[self.query.chat_id]))
        self.assertContains(page, "Consensus search timed out")
        self.assertNotContains(page, "consensus-test-secret")

    def test_consensus_success_makes_query_usable_when_all_models_fail(self):
        self.post.side_effect = requests.Timeout()
        run_research(self.query)
        self.assertEqual(self.query.status, ResearchQuery.Status.COMPLETED)
        self.assertEqual(Citation.objects.count(), 1)

    def test_all_providers_fail(self):
        self.post.side_effect = requests.Timeout()
        self.get.side_effect = requests.Timeout()
        run_research(self.query)
        self.assertEqual(self.query.status, ResearchQuery.Status.FAILED)
        self.assertIsNotNone(self.query.completed_at)
        self.assertEqual(AgentResponse.objects.count(), 4)

    def test_empty_search_is_completed_with_no_citations(self):
        self.get.return_value = search_response(search_fixture([]))
        self.post.side_effect = requests.Timeout()
        run_research(self.query)
        self.assertEqual(self.query.status, ResearchQuery.Status.COMPLETED)
        self.assertFalse(Citation.objects.exists())
        self.assertContains(self.client.get(reverse("chats:detail", args=[self.query.chat_id])), "Consensus found no papers")

    def test_persistence_deduplicates_existing_doi_or_url(self):
        existing = Citation.objects.create(research_query=self.query, title="Existing", url="https://example.org/old", source_name="", metadata={"doi": "https://doi.org/10.1234/EXAMPLE.SLEEP"})
        normalized = normalize_search(search_fixture(), self.query.question)
        persist_evidence(self.query, normalized)
        persist_evidence(self.query, normalized)
        self.assertEqual(Citation.objects.count(), 1)
        self.assertEqual(AgentResponse.objects.count(), 1)
        existing.refresh_from_db()
        self.assertEqual(existing.metadata["year"], 2025)
        self.assertEqual(existing.source_name, "Example Journal")

    def test_duplicate_api_papers_do_not_create_duplicate_citations(self):
        self.get.return_value = search_response(search_fixture([paper_fixture(), paper_fixture(doi="", url="https://example.org/papers/sleep#ref")]))
        run_research(self.query)
        self.assertEqual(Citation.objects.count(), 1)

    def test_deduplication_is_per_research_query(self):
        run_research(self.query)
        other = save_question("Another question", self.query.chat)
        run_research(other)
        self.assertEqual(Citation.objects.count(), 2)

    def test_year_only_and_no_identifiers_are_preserved_without_invention(self):
        result = normalize_search(search_fixture([{"title": "Paper without a link", "publish_year": 2024}]), self.query.question)
        persist_evidence(self.query, result)
        persist_evidence(self.query, result)
        self.assertEqual(Citation.objects.count(), 1)
        citation = Citation.objects.get()
        self.assertIsNone(citation.published_date)
        self.assertEqual(citation.metadata["year"], 2024)
        self.assertEqual(citation.url, "")

    def test_persistence_failure_is_isolated_and_recorded(self):
        with patch("research.evidence.Citation.objects.create", side_effect=ValueError("consensus-test-secret")):
            run_research(self.query)
        self.assertEqual(self.query.status, ResearchQuery.Status.COMPLETED)
        self.assertEqual(self.query.agent_responses.count(), 4)
        self.assertEqual(self.query.agent_responses.get(provider="consensus").normalized_response["error"]["code"], "persistence")

    def test_dashboard_sections_are_separate_and_links_safe(self):
        run_research(self.query)
        page = self.client.get(reverse("chats:detail", args=[self.query.chat_id]))
        self.assertContains(page, "AI Agent Research")
        self.assertContains(page, "Academic Evidence")
        self.assertContains(page, "1 unique paper")
        self.assertContains(page, "A. Example, B. Example")
        self.assertContains(page, "Citations: 0")
        self.assertContains(page, 'href="https://example.org/papers/sleep" target="_blank" rel="noopener noreferrer"')
        content = page.content.decode()
        ai_section, academic_section = content.split('aria-label="AI Agent Research"', 1)[1].split('aria-label="Academic Evidence"', 1)
        self.assertNotIn(paper_fixture()["title"], ai_section)
        self.assertIn(paper_fixture()["title"], academic_section)

    def test_malicious_provider_text_and_links_are_not_rendered_as_html(self):
        self.get.return_value = search_response(search_fixture([paper_fixture(title="<script>alert(1)</script>", url="javascript:alert(1)")]))
        run_research(self.query)
        self.assertEqual(Citation.objects.get().url, "")
        page = self.client.get(reverse("chats:detail", args=[self.query.chat_id]))
        self.assertContains(page, "&lt;script&gt;")
        self.assertNotContains(page, "javascript:")
        self.assertNotContains(page, "<script>alert")

    def test_failed_authentication_does_not_expose_credentials(self):
        self.get.return_value = search_response({"detail": "consensus-test-secret", "x-api-key": "consensus-test-secret"}, status=401)
        run_research(self.query)
        self.assertNotIn("consensus-test-secret", str(list(AgentResponse.objects.values())))
        page = self.client.get(reverse("chats:detail", args=[self.query.chat_id]))
        self.assertNotContains(page, "consensus-test-secret")

    def test_malformed_response_is_saved_without_losing_model_results(self):
        self.get.return_value = search_response({"unexpected": "format"})
        run_research(self.query)
        self.assertEqual(self.query.status, ResearchQuery.Status.COMPLETED)
        self.assertEqual(self.query.agent_responses.count(), 4)
        evidence = self.query.agent_responses.get(provider="consensus")
        self.assertEqual(evidence.normalized_response["error"]["code"], "malformed_response")
        self.assertEqual(json.loads(evidence.raw_response), {"unexpected": "format"})

    def test_finished_query_is_not_run_again(self):
        run_research(self.query)
        run_research(self.query)
        self.get.assert_called_once()
        self.assertEqual(self.post.call_count, 3)
