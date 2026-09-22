"""Exercise rendered form wiring over real local HTTP, with an isolated test DB."""
from html.parser import HTMLParser

import requests
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings

from .models import Chat, Message
from .services import save_question
from research.models import ResearchQuery, AgentResponse, Citation, FinalResponse


class PageControls(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.actions = []
        self.forms = {}
        self.scripts = []
        self.tokens = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "button" and "data-chat-action" in attrs:
            self.actions.append(attrs)
        if tag == "form":
            self.forms[attrs.get("id")] = attrs
        if tag == "script" and attrs.get("src"):
            self.scripts.append(attrs["src"])
        if tag == "input" and attrs.get("name") == "csrfmiddlewaretoken":
            self.tokens.append(attrs["value"])


@override_settings(CSRF_COOKIE_SECURE=False, SESSION_COOKIE_SECURE=False)
class ChatBrowserEndpointTests(StaticLiveServerTestCase):
    def setUp(self):
        self.session = requests.Session()
        self.session.trust_env = False
        self.addCleanup(self.session.close)
        self.query = save_question("Browser target research")
        self.other = Chat.objects.create(title="Must survive")
        agent = AgentResponse.objects.create(research_query=self.query, provider="openrouter", model_name="test/model")
        Citation.objects.create(research_query=self.query, agent_response=agent, title="Saved citation")
        FinalResponse.objects.create(research_query=self.query, answer="Saved answer")

    def load_page(self):
        response = self.session.get(f"{self.live_server_url}/chats/{self.query.chat_id}/", timeout=10)
        self.assertEqual(response.status_code, 200)
        page = PageControls(response.text)
        self.assertEqual(page.forms["chat-management-form"]["method"], "post")
        self.assertTrue(page.tokens)
        return page

    def action(self, page, action):
        return next(a for a in page.actions if a["data-chat-action"] == action and (action == "clear" or a["data-title"] == "Browser target research"))

    def post(self, url, data):
        return self.session.post(self.live_server_url + url, data=data, timeout=10, allow_redirects=False)

    def test_served_javascript_has_current_management_and_theme_handlers(self):
        page = self.load_page()
        for name, expected in [("dashboard.js", "data-chat-action"), ("theme.js", "research-theme")]:
            path = next(path for path in page.scripts if name in path)
            self.assertIn("?v=", path)
            response = self.session.get(self.live_server_url + path, timeout=10)
            self.assertEqual(response.status_code, 200)
            self.assertIn("javascript", response.headers["Content-Type"])
            self.assertIn(expected, response.text)

    def test_rename_posts_to_rendered_selected_chat_url(self):
        page = self.load_page()
        action = self.action(page, "rename")
        response = self.post(action["data-url"], {"csrfmiddlewaretoken": page.tokens[0], "title": "Renamed through HTTP"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], f"/chats/{self.query.chat_id}/")
        self.assertEqual(Chat.objects.get(pk=self.query.chat_id).title, "Renamed through HTTP")
        self.other.refresh_from_db()
        self.assertEqual(self.other.title, "Must survive")

    def test_delete_targets_exact_chat_and_redirects_to_empty_state(self):
        page = self.load_page()
        action = self.action(page, "delete")
        self.assertEqual(action["data-chat-id"], str(self.query.chat_id))
        response = self.post(action["data-url"], {"csrfmiddlewaretoken": page.tokens[0], "confirm_chat": action["data-chat-id"]})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/")
        self.assertTrue(Chat.objects.filter(pk=self.other.pk).exists())
        for model in (ResearchQuery, Message, AgentResponse, Citation, FinalResponse):
            self.assertFalse(model.objects.exists())
        landing = self.session.get(self.live_server_url + "/", timeout=10)
        self.assertIn("Start a new research question", landing.text)
        self.assertNotIn('data-title="Browser target research"', landing.text)

    def test_delete_rejects_missing_csrf_and_mismatched_confirmation(self):
        page = self.load_page()
        action = self.action(page, "delete")
        self.assertEqual(self.post(action["data-url"], {"confirm_chat": action["data-chat-id"]}).status_code, 403)
        self.assertEqual(self.post(action["data-url"], {"csrfmiddlewaretoken": page.tokens[0], "confirm_chat": str(self.other.pk)}).status_code, 400)
        self.assertEqual(Chat.objects.count(), 2)

    def test_clear_posts_only_after_exact_confirmation(self):
        page = self.load_page()
        action = self.action(page, "clear")
        data = {"csrfmiddlewaretoken": page.tokens[0], "confirmation": "yes"}
        self.assertEqual(self.post(action["data-url"], data).status_code, 400)
        self.assertEqual(Chat.objects.count(), 2)
        data["confirmation"] = "DELETE ALL CHATS"
        self.assertEqual(self.post(action["data-url"], data).status_code, 302)
        self.assertFalse(Chat.objects.exists())
