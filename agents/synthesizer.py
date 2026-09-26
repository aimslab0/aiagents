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
        prompt = JUDGE_PROMPT + (TOPIC_JUDGE_PROMPT if is_topic_discovery(context.get('question', '')) else '')
        schema = deepcopy(SYNTHESIS_SCHEMA)
        if 'research_plan' in context:
            prompt += '''\nPlan-and-Solve: planners only suggest searches and speculative gap hypotheses,
not evidence. Reason from academic_evidence only. C, S and M IDs are all academic
sources (Consensus, Semantic Scholar, or both). Cite only allowed_source_ids.
Identify contradictions. No evidence found does not mean evidence of falsehood.
Never claim a gap just because few papers were retrieved. Distinguish established
evidence from plausible gaps. Executive summary: 100-150 words. At most five findings
and five limitations. Avoid repeating evidence. For thesis-topic discovery propose
at most three topics in final_answer: research problem, why each matters, existing
literature, unresolved question, population, variables, methodology, feasibility,
supporting supplied source IDs and limitations. Do not fabricate bibliography entries.
'''
            schema['properties']['key_findings']['maxItems'] = 5
            schema['properties']['limitations']['maxItems'] = 5
        if context.get('deep_research'):
            from .deep_synthesis import DEEP_PROMPT, DEEP_SCHEMA
            prompt, schema = DEEP_PROMPT, DEEP_SCHEMA
        context_limit = context['synthesis_budget']['context_chars'] if context.get('deep_research') else settings.SYNTHESIS_MAX_CONTEXT_CHARS
        context_size = len(serialized.encode('utf-8')) if context.get('deep_research') else len(serialized)
        if context_size > context_limit:
            raise SynthesisError("context_limit")
        response_format = {"type": "json_schema", "json_schema": {"name": "final_synthesis", "strict": True, "schema": schema}}
        if context.get('deep_research'):
            option = next((o for o in settings.DEEP_SYNTHESIZER_OPTIONS if o['id'] == self.model_id), None)
            if option and option.get('response_format') == 'json_object':
                # R1 advertises JSON mode, not native JSON-schema enforcement.
                response_format = {'type': 'json_object'}
                prompt += '\nRequired JSON schema (validated by the application):\n' + json.dumps(schema)
        try:
            response = requests.post(
                API_URL,
                headers={"Authorization": f"Bearer {settings.OPENROUTER_API_KEY}"},
                json={
                    "model": self.model_id,
                    "messages": [{"role": "system", "content": prompt}, {"role": "user", "content": serialized}],
                    "response_format": response_format,
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
