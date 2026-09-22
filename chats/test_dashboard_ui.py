from html.parser import HTMLParser
from unittest.mock import patch

from django.test import Client, TestCase, override_settings
from django.urls import reverse

from research.models import AgentResponse, Citation, FinalResponse, ResearchQuery
from .models import Chat, Message
from .services import save_question


class DashboardStructure(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.details = []
        self.links = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == "details":
            self.details.append(dict(attrs))
        if tag == "a":
            self.links.append(dict(attrs))


class ChatManagementTests(TestCase):
    def setUp(self):
        self.query = save_question("Sleep research")
        self.chat = self.query.chat
        self.other = Chat.objects.create(title="Another research")

    def test_dashboard_search_theme_and_history(self):
        page = self.client.get(reverse("chats:detail", args=[self.chat.pk]))
        for label in ("Search chats", "Sleep research", "Another research", "New Research", "System", "Light", "Dark", 'aria-current="page"'):
            self.assertContains(page, label)

    def test_rename_is_scoped_and_validated(self):
        url = reverse("chats:rename", args=[self.chat.pk])
        for title in ("", " ", "x" * 201):
            self.assertEqual(self.client.post(url, {"title": title}).status_code, 400)
        self.assertRedirects(self.client.post(url, {"title": "  Renamed research  "}), reverse("chats:detail", args=[self.chat.pk]))
        self.chat.refresh_from_db()
        self.other.refresh_from_db()
        self.assertEqual(self.chat.title, "Renamed research")
        self.assertEqual(self.other.title, "Another research")

    def test_delete_active_chat_cascades_only_selected_chat(self):
        agent = AgentResponse.objects.create(research_query=self.query, provider="openrouter", model_name="test")
        Citation.objects.create(research_query=self.query, agent_response=agent, title="Citation")
        FinalResponse.objects.create(research_query=self.query, answer="Answer")
        page = self.client.post(reverse("chats:delete", args=[self.chat.pk]), {"confirm_chat": str(self.chat.pk)}, follow=True)
        self.assertContains(page, "Start a new research question")
        self.assertFalse(ResearchQuery.objects.exists())
        self.assertFalse(Message.objects.exists())
        self.assertFalse(AgentResponse.objects.exists())
        self.assertFalse(Citation.objects.exists())
        self.assertFalse(FinalResponse.objects.exists())
        self.assertTrue(Chat.objects.filter(pk=self.other.pk).exists())

    def test_wrong_or_missing_delete_confirmation_preserves_chats(self):
        for payload in ({}, {"confirm_chat": str(self.other.pk)}):
            self.assertEqual(self.client.post(reverse("chats:delete", args=[self.chat.pk]), payload).status_code, 400)
        self.assertEqual(Chat.objects.count(), 2)
        self.assertEqual(self.client.post(reverse("chats:delete", args=[999999]), {"confirm_chat": "999999"}).status_code, 404)

    def test_clear_requires_explicit_confirmation(self):
        for payload in ({}, {"confirmation": "yes"}):
            self.assertEqual(self.client.post(reverse("chats:clear"), payload).status_code, 400)
        self.assertEqual(Chat.objects.count(), 2)
        self.assertRedirects(self.client.post(reverse("chats:clear"), {"confirmation": "DELETE ALL CHATS"}), reverse("chats:dashboard"))
        self.assertFalse(Chat.objects.exists())

    def test_management_is_post_only_and_csrf_protected(self):
        secure = Client(enforce_csrf_checks=True)
        for name, args in (("rename", [self.chat.pk]), ("delete", [self.chat.pk]), ("clear", [])):
            url = reverse(f"chats:{name}", args=args)
            self.assertEqual(self.client.get(url).status_code, 405)
            self.assertEqual(secure.post(url, {}).status_code, 403)


@override_settings(RESEARCH_DIAGNOSTICS_ENABLED=True)
class ResearchPresentationTests(TestCase):
    def setUp(self):
        network = patch("requests.sessions.Session.request", side_effect=AssertionError("No live requests allowed"))
        network.start()
        self.addCleanup(network.stop)
        self.query = save_question("Exact research question?")
        self.query.status = "completed"
        self.query.stage = "COMPLETED"
        self.query.save()
        self.agent = AgentResponse.objects.create(research_query=self.query, provider="openrouter", provider_key="gpt", model_name="stored/model", succeeded=True, normalized_response={"label": "GPT", "answer": "Full individual response preserved", "key_findings": []})
        self.consensus = AgentResponse.objects.create(research_query=self.query, provider="consensus", provider_key="consensus", model_name="search-v1", succeeded=True, normalized_response={"papers": []})
        papers, sources, priorities = [], [], []
        for index in range(5):
            paper = {"title": f"Paper {index}", "url": f"https://example.org/paper/{index}", "authors": ["A. Author"], "year": 2025, "journal": "Journal", "abstract": "Long abstract. " * 100, "citation_count": 0}
            citation = Citation.objects.create(research_query=self.query, agent_response=self.consensus, title=paper["title"], url=paper["url"], metadata=paper)
            self.consensus.citations_used.add(citation)
            papers.append(paper)
            sources.append({"source_id": f"C{citation.pk}", "citation_id": citation.pk, "kind": "academic", "title": paper["title"], "url": paper["url"]})
            priorities.append({"source_id": f"C{citation.pk}", "total": index})
        self.consensus.normalized_response = {"papers": papers}
        self.consensus.save()
        self.final = FinalResponse.objects.create(research_query=self.query, answer="Qualified final answer. " * 60, confidence=0, synthesis_data={"status": "completed", "sources": sources, "source_ids": [s["source_id"] for s in sources], "key_findings": [{"claim": f"Finding {i}", "evidence_strength": "moderate", "supporting_source_ids": [sources[0]["source_id"]]} for i in range(7)], "diagnostics": {"paper_priorities": priorities}, "limitations": ["Limited sample"], "disagreements": ["Different conclusions"]})
        self.url = reverse("chats:detail", args=[self.query.chat_id])

    def test_snapshot_findings_papers_sources_and_disagreements(self):
        page = self.client.get(self.url)
        for text in ("Exact research question?", "Qualified final answer", "Read full answer", "View all findings (7)", "Finding 6", "View all papers (5)", "Paper 4", "Different conclusions", "Limited sample", "0%", "Synthesizer confidence estimate", "Full individual response preserved"):
            self.assertContains(page, text)
        self.assertEqual([p["title"] for p in page.context["research_queries"][0].ranked_papers], [f"Paper {i}" for i in reversed(range(5))])

    def test_details_collapsed_and_external_links_safe(self):
        parsed = DashboardStructure(self.client.get(self.url).content.decode())
        self.assertGreater(len(parsed.details), 10)
        self.assertTrue(all("open" not in attrs for attrs in parsed.details))
        for link in parsed.links:
            if link.get("href", "").startswith("https://example.org"):
                self.assertEqual(link["target"], "_blank")
                self.assertEqual(link["rel"], "noopener noreferrer")

    @override_settings(RESEARCH_DIAGNOSTICS_ENABLED=False)
    def test_diagnostics_hidden_but_research_and_retries_retained(self):
        page = self.client.get(self.url)
        for text in ("Developer diagnostics", "Developer Diagnostics", "Credential readiness", "total_estimated_openrouter_cost"):
            self.assertNotContains(page, text)
        self.assertContains(page, "Full individual response preserved")
        self.assertContains(page, "Re-run Final Synthesis")

    def test_malformed_source_urls_and_html_remain_inert(self):
        data = self.final.synthesis_data
        data["sources"][0].update(url="javascript:alert(1)", title="<script>unsafe()</script>")
        self.final.synthesis_data = data
        self.final.save()
        page = self.client.get(self.url)
        self.assertNotContains(page, 'href="javascript:')
        self.assertNotContains(page, "<script>unsafe")
        self.assertContains(page, "&lt;script&gt;unsafe")

    def test_partial_and_synthesis_failure_are_visible(self):
        self.final.delete()
        self.query.stage = "PARTIAL"
        self.query.save()
        page = self.client.get(self.url)
        self.assertContains(page, "Partial research available")
        self.assertContains(page, "Final synthesis unavailable. Collected research is still available.")

    def test_no_papers_and_processing_states(self):
        self.final.delete()
        self.consensus.delete()
        self.query.status = "processing"
        self.query.save()
        page = self.client.get(self.url)
        self.assertContains(page, "No academic papers were returned for this query.")
        self.assertContains(page, "Researching across multiple models and academic evidence...")
