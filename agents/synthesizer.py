import json
from copy import deepcopy

import requests
from django.conf import settings
from django.views.decorators.debug import sensitive_variables

from .openrouter import API_URL
from .schemas import strict_json_loads
from .security import redact
from .budgets import budget_for
from .synthesis_schemas import JUDGE_PROMPT, SYNTHESIS_SCHEMA, SynthesisError
from .topic_discovery import is_topic_discovery, TOPIC_JUDGE_PROMPT


class SynthesizerClient:
    def __init__(self, model_id=None):
        self.model_id = model_id or settings.SYNTHESIZER_MODEL
        self.raw_metadata = {}

    @sensitive_variables()
    def synthesize(self, context):
        self.raw_metadata = {}
        if not budget_for("synthesis")["enabled"] or not settings.OPENROUTER_API_KEY or not settings.SYNTHESIZER_MODEL:
            raise SynthesisError("configuration")
        # Omitted papers are diagnostics, not citable evidence. Their IDs previously
        # leaked into the prompt and could be echoed as invalid citations.
        payload = deepcopy(context)
        allowed_ids = [item["source_id"] for group in ("agent_findings", "academic_evidence")
                       for item in payload.get(group, []) if "source_id" in item]
        payload["allowed_source_ids"] = allowed_ids
        for notice in payload.get("context_limits", {}).get("truncations_and_omissions", []):
            if notice.get("source_id") not in allowed_ids:
                notice.pop("source_id", None)
        serialized = json.dumps(redact(payload), ensure_ascii=False, allow_nan=False)
        if len(serialized) > settings.SYNTHESIS_MAX_CONTEXT_CHARS:
            raise SynthesisError("context_limit")
        try:
            response = requests.post(
                API_URL,
                headers={"Authorization": f"Bearer {settings.OPENROUTER_API_KEY}"},
                json={
                    "model": self.model_id,
                    "messages": [{"role": "system", "content": JUDGE_PROMPT + (TOPIC_JUDGE_PROMPT if is_topic_discovery(context.get("question", "")) else "")}, {"role": "user", "content": serialized}],
                    "response_format": {"type": "json_schema", "json_schema": {"name": "final_synthesis", "strict": True, "schema": SYNTHESIS_SCHEMA}},
                    "provider": settings.OPENROUTER_PROVIDER_OPTIONS,
                    "max_tokens": budget_for("synthesis")["max_tokens"], "stream": False,
                },
                timeout=(settings.OPENROUTER_CONNECT_TIMEOUT, budget_for("synthesis")["timeout"]),
                allow_redirects=False,
            )
        except requests.Timeout:
            raise SynthesisError("timeout") from None
        except requests.RequestException:
            raise SynthesisError("connection") from None
        try:
            try:
                body = strict_json_loads(response.text)
                if isinstance(body, dict):
                    self.raw_metadata = {"usage": body.get("usage", {})}
            except (ValueError, TypeError, RecursionError):
                body = {}
            if not 200 <= response.status_code < 300:
                raise SynthesisError("api_error", status=response.status_code)
            if body.get("error"):
                code = body["error"].get("code") if isinstance(body["error"], dict) else None
                status = int(code) if str(code).isdigit() else None
                raise SynthesisError("api_error", status=status)
            choice = body["choices"][0]
            if choice.get("error") or choice.get("finish_reason") == "error":
                raise SynthesisError("api_error")
            if choice.get("finish_reason") == "length":
                raise SynthesisError("truncated_response")
            if choice.get("finish_reason") == "content_filter" or choice["message"].get("refusal"):
                raise SynthesisError("refusal")
            content = choice["message"]["content"]
            if not isinstance(content, str):
                raise ValueError
            return redact(content)
        except (ValueError, TypeError, KeyError, IndexError, AttributeError, RecursionError):
            raise SynthesisError("malformed_response") from None
        finally:
            response.close()
