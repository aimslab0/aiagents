import importlib.util
import logging
import os
import sys
from unittest.mock import patch

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from .logging import SafeConsoleFormatter


class DeploymentSettingsTests(SimpleTestCase):
    def load_settings(self, env):
        with patch.dict(os.environ, env, clear=True), patch('dotenv.load_dotenv') as load:
            spec = importlib.util.spec_from_file_location(
                'research_ai._deployment_test', settings.BASE_DIR / 'research_ai/settings.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            load.assert_called_once_with(module.BASE_DIR / '.env', override=False, interpolate=False)
            return module

    def test_production_environment_and_paths(self):
        module = self.load_settings({
            'SECRET_KEY': 'test-only', 'DEBUG': 'False',
            'ALLOWED_HOSTS': ' example.pythonanywhere.com, example.com, ',
            'CSRF_TRUSTED_ORIGINS': 'https://example.com, https://example.pythonanywhere.com',
        })
        self.assertFalse(module.DEBUG)
        self.assertTrue(module.CSRF_COOKIE_SECURE)
        self.assertTrue(module.SESSION_COOKIE_SECURE)
        self.assertEqual(module.ALLOWED_HOSTS, ['example.pythonanywhere.com', 'example.com'])
        self.assertEqual(module.CSRF_TRUSTED_ORIGINS, ['https://example.com', 'https://example.pythonanywhere.com'])
        self.assertEqual(module.STATIC_ROOT, module.BASE_DIR / 'staticfiles')
        self.assertEqual(module.DATABASES['default']['NAME'], module.BASE_DIR / 'db.sqlite3')

    def test_local_http_remains_supported(self):
        module = self.load_settings({'SECRET_KEY': 'test-only', 'DEBUG': 'True'})
        self.assertTrue(module.DEBUG)
        self.assertFalse(module.CSRF_COOKIE_SECURE)
        self.assertFalse(module.SESSION_COOKIE_SECURE)
        self.assertIn('localhost', module.ALLOWED_HOSTS)
        self.assertFalse(getattr(module, 'SECURE_SSL_REDIRECT', False))

    def test_debug_defaults_false(self):
        self.assertFalse(self.load_settings({'SECRET_KEY': 'test-only'}).DEBUG)

    def test_missing_secret_fails_closed(self):
        with self.assertRaises(ImproperlyConfigured):
            self.load_settings({})


@override_settings(DEBUG=False, SECRET_KEY='private-django-secret',
                   OPENROUTER_API_KEY='private-openrouter-key', CONSENSUS_API_KEY='private-consensus-key')
class SafeLoggingTests(SimpleTestCase):
    def test_redacts_credentials_and_preserves_stage(self):
        record = logging.LogRecord('research.synthesis', logging.ERROR, '', 1,
            'stage=failed %s', ('private-django-secret private-openrouter-key private-consensus-key Bearer unknown-token',), None)
        result = SafeConsoleFormatter().format(record)
        self.assertIn('stage=failed', result)
        for secret in ('private-django-secret', 'private-openrouter-key', 'private-consensus-key', 'unknown-token'):
            self.assertNotIn(secret, result)

    def test_exception_payload_not_logged_and_record_unchanged(self):
        try:
            raise ValueError('private response payload')
        except ValueError:
            record = logging.LogRecord('research.execution', logging.ERROR, '', 1, 'persistence failed', (), sys.exc_info())
        result = SafeConsoleFormatter().format(record)
        self.assertIn('ValueError', result)
        self.assertIn('frames=', result)
        self.assertNotIn('private response payload', result)
        self.assertIsNotNone(record.exc_info)

    def test_framework_urls_are_not_logged(self):
        record = logging.LogRecord('django.request', logging.ERROR, '', 1, 'Error /?credential=private', (), None)
        record.status_code = 500
        self.assertEqual(SafeConsoleFormatter().format(record), 'HTTP event status=500')
