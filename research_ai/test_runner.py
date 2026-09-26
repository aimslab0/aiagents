"""Fail closed on unmocked external HTTP while allowing local browser endpoint tests."""
from unittest.mock import patch
from urllib.parse import urlsplit
import requests
from django.test.runner import DiscoverRunner


class OfflineTestRunner(DiscoverRunner):
    def run_tests(self, *args, **kwargs):
        original = requests.sessions.Session.request
        def guarded(session, method, url, **options):
            if urlsplit(url).hostname not in {'localhost', '127.0.0.1', '::1'}:
                raise AssertionError('External HTTP must be mocked in automated tests.')
            return original(session, method, url, **options)
        with patch('requests.sessions.Session.request', guarded):
            return super().run_tests(*args, **kwargs)
