import ipaddress
import json
import re
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator


def redact(value):
    """Remove provider credentials even when an upstream response echoes them."""
    if isinstance(value, dict):
        return {
            redact(key): "[REDACTED]" if key.lower() in {
                "authorization", "api_key", "apikey", "x-api-key",
                "openrouter_api_key", "consensus_api_key", "semantic_scholar_api_key",
            } else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        for name in ("OPENROUTER_API_KEY", "CONSENSUS_API_KEY", "SEMANTIC_SCHOLAR_API_KEY"):
            secret = getattr(settings, name, "")
            if secret:
                value = value.replace(secret, "[REDACTED]")
                value = value.replace(json.dumps(secret)[1:-1], "[REDACTED]")
        return re.sub(r"sk-or-[A-Za-z0-9_-]+", "[REDACTED]", value)
    return value


def safe_source_url(value):
    if not isinstance(value, str) or len(value) > 2000 or re.search(r"[\x00-\x20\x7f]", value):
        return ""
    try:
        URLValidator(schemes=["http", "https"])(value)
        parts = urlsplit(value)
        host = (parts.hostname or "").lower().rstrip(".")
        if parts.username or parts.password or host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            return ""
        try:
            if not ipaddress.ip_address(host).is_global:
                return ""
        except ValueError:
            pass
    except (ValidationError, ValueError):
        return ""
    return value
