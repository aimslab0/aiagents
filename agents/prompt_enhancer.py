"""Isolated, single-model query rewriting; never creates research records."""
import re
import logging
import time

import requests
from django.conf import settings

from .openrouter import API_URL
from .schemas import strict_json_loads
from .security import redact

logger = logging.getLogger(__name__)

MAX_INPUT_CHARS = 2000
MAX_OUTPUT_TOKENS = 256
TIMEOUT = (5, 20)
SYSTEM_PROMPT = "You are an expert academic writing assistant. The user will provide a casual, vague, or poorly phrased research idea. Rewrite it into a single, highly professional, Master-level academic research question suitable for a database search. Do not answer the question. Do not add conversational filler. Output ONLY the rewritten academic text."


class EnhancementError(Exception):
    def __init__(self, category="malformed_response"):
        self.category = category
        super().__init__("Prompt enhancement is temporarily unavailable. Your original text is unchanged. Please try again later.")


def clean_output(text):
    if not isinstance(text, str):
        raise EnhancementError()
    text = text.strip()
    for _ in range(3):
        text = re.sub(r"\A(?:enhanced\s+(?:prompt|question)|rewritten\s+(?:prompt|question)|academic\s+(?:research\s+)?question)\s*:\s*", "", text, flags=re.I).strip()
        text = re.sub(r"\A```[^\n]*\n(.*?)\n?```\Z", r"\1", text, flags=re.DOTALL).strip()
        if len(text) >= 2 and (text[0], text[-1]) in {('"', '"'), ("'", "'"), ('“', '”'), ('‘', '’')}:
            text = text[1:-1].strip()
    text = " ".join(text.split())
    if not text or len(text) > MAX_INPUT_CHARS:
        raise EnhancementError()
    return text


def provider_error(status, data):
    category = {400: "invalid_request", 401: "authentication", 402: "credits", 403: "access_denied", 404: "model_unavailable", 408: "timeout", 429: "rate_limit"}.get(status, "provider_error")
    error = data.get("error", {}) if isinstance(data, dict) else {}
    message = error.get("message", "No provider error message") if isinstance(error, dict) else "Unstructured provider error"
    message = redact(message) if isinstance(message, str) else "Invalid provider message"
    message = re.sub(r"(?i)Bearer\s+[^\s,;]+", "Bearer [REDACTED]", message)
    message = " ".join(message.split())[:500]
    logger.warning("enhancer provider_failure status=%s category=%s message=%s", status, category, message)
    return EnhancementError(category)


def final_text(choice):
    message = choice.get("message") or {}
    if not isinstance(message, dict):
        raise EnhancementError()
    if message.get("refusal") or choice.get("finish_reason") in {"length", "content_filter", "error", "tool_calls"}:
        raise EnhancementError(category="incomplete_or_refused")
    content = message.get("content")
    if isinstance(content, list):
        content = " ".join(part["text"] for part in content if isinstance(part, dict) and part.get("type") in {"text", "output_text"} and isinstance(part.get("text"), str))
    # Reasoning is not the final rewrite and must never be exposed as one.
    content = content or message.get("output_text") or choice.get("text")
    if not content and (message.get("reasoning") or message.get("reasoning_details")):
        raise EnhancementError(category="reasoning_without_final_text")
    return clean_output(content)


def _request(text, model):
    response = None
    started = time.monotonic()
    logger.info("enhancer start model=%s", model)
    try:
        response = requests.post(
            API_URL,
            headers={"Authorization": f"Bearer {settings.OPENROUTER_API_KEY}"},
            json={
                "model": model,
                "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": text}],
                "temperature": 0.2, "max_tokens": MAX_OUTPUT_TOKENS,
                "stream": False, "reasoning": {"enabled": False},
                "provider": {"allow_fallbacks": False},
            },
            timeout=TIMEOUT, allow_redirects=False,
        )
        logger.info("enhancer response model=%s http_status=%s", model, response.status_code)
        try:
            data = strict_json_loads(response.text)
        except (ValueError, RecursionError):
            if response.status_code != 200:
                raise provider_error(response.status_code, {}) from None
            raise EnhancementError() from None
        if response.status_code != 200:
            raise provider_error(response.status_code, data)
        if isinstance(data, dict) and "error" in data:
            error = data["error"]
            try:
                code = int(error.get("code", 502)) if isinstance(error, dict) else 502
            except (ValueError, TypeError):
                code = 502
            raise provider_error(code, data)
        choice = data["choices"][0]
        return final_text(choice)
    except requests.Timeout:
        raise EnhancementError("timeout") from None
    except requests.RequestException:
        raise EnhancementError("network_failure") from None
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, RecursionError):
        raise EnhancementError() from None
    finally:
        logger.info("enhancer attempt_finished model=%s latency_ms=%s", model, round((time.monotonic() - started) * 1000))
        if response is not None:
            response.close()


def enhance_prompt(text):
    if not settings.OPENROUTER_API_KEY:
        logger.error("enhancer final_failure category=configuration key_loaded=false")
        raise EnhancementError(category="configuration")
    model = settings.PROMPT_ENHANCER_MODEL
    try:
        result = {"enhanced_text": _request(text, model), "model_used": model}
        logger.info("enhancer success model=%s", model)
        return result
    except EnhancementError as error:
        logger.error("enhancer final_failure model=%s category=%s", model, error.category)
        raise
