import requests
from django.conf import settings
from django.views.decorators.debug import sensitive_variables

from .base import ResearchAgent
from .exceptions import AgentError
from .schemas import RESEARCH_SCHEMA, SYSTEM_PROMPT, normalize_content, strict_json_loads
from .security import redact
from .budgets import budget_for, provider_for_model
from .topic_discovery import is_topic_discovery, TOPIC_PROMPT, TOPIC_SCHEMA, normalize_topic_content

API_URL = "https://openrouter.ai/api/v1/chat/completions"


def api_error_code(status):
    return {
        401: "authentication", 402: "credits", 408: "timeout",
        429: "rate_limit", 504: "timeout",
    }.get(status, "api_error")


class OpenRouterClient(ResearchAgent):
    def __init__(self, max_tokens=None, provider_key=None):
        self.max_tokens = max_tokens
        self.provider_key = provider_key

    @sensitive_variables()
    def research(self, question, model_id):
        budget = budget_for(self.provider_key or provider_for_model(model_id))
        if not budget["enabled"] or not settings.OPENROUTER_API_KEY:
            raise AgentError("configuration")
        topic_discovery = is_topic_discovery(question)
        payload = {
            "model": model_id,
            "messages": [
                {"role": "system", "content": TOPIC_PROMPT if topic_discovery else SYSTEM_PROMPT},
                {"role": "user", "content": question},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "topic_discovery" if topic_discovery else "research_response", "strict": True, "schema": TOPIC_SCHEMA if topic_discovery else RESEARCH_SCHEMA},
            },
            "provider": settings.OPENROUTER_PROVIDER_OPTIONS,
            "max_tokens": self.max_tokens if self.max_tokens is not None else budget["max_tokens"],
            "stream": False,
        }
        try:
            response = requests.post(
                API_URL,
                headers={"Authorization": f"Bearer {settings.OPENROUTER_API_KEY}"},
                json=payload,
                timeout=(settings.OPENROUTER_CONNECT_TIMEOUT, budget["timeout"]),
                allow_redirects=False,
            )
        except requests.Timeout:
            raise AgentError("timeout") from None
        except requests.RequestException:
            raise AgentError("connection") from None

        try:
            body = redact(strict_json_loads(response.text))
        except (ValueError, TypeError, RecursionError):
            body = {"body": redact(response.text)}
        finally:
            response.close()
        raw = body if isinstance(body, dict) else {"body": body}
        if not 200 <= response.status_code < 300:
            raise AgentError(api_error_code(response.status_code), raw_response=raw, status=response.status_code)
        if isinstance(raw.get("error"), dict):
            code = raw["error"].get("code")
            status = int(code) if str(code).isdigit() else None
            raise AgentError(api_error_code(status), raw_response=raw, status=status)

        try:
            choice = raw["choices"][0]
            if choice.get("error") or choice.get("finish_reason") == "error":
                raise AgentError("api_error", raw_response=raw)
            if choice.get("finish_reason") == "length":
                raise AgentError("truncated_response", raw_response=raw)
            message = choice["message"]
            if message.get("refusal") or choice.get("finish_reason") == "content_filter":
                raise AgentError("refusal", raw_response=raw)
            content = message["content"]
            if not isinstance(content, str):
                raise TypeError
        except (KeyError, IndexError, TypeError, AttributeError):
            raise AgentError("malformed_response", raw_response=raw) from None
        normalize = normalize_topic_content if topic_discovery else normalize_content
        return redact(normalize(content, model_id, raw))
