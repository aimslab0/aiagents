from collections import Counter
from decimal import Decimal, InvalidOperation
from time import perf_counter

from django.conf import settings
from django.utils import timezone


def credentials_ready():
    return {"OpenRouter": bool(settings.OPENROUTER_API_KEY), "Consensus": bool(settings.CONSENSUS_API_KEY)}


def decimal_value(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and 0 <= result < 100000000 else None
    except (InvalidOperation, ValueError):
        return None


def usage_metrics(raw, model):
    usage = raw.get("usage", {}) if isinstance(raw, dict) else {}
    usage = usage if isinstance(usage, dict) else {}
    result = {}
    for field, key in (("input_tokens", "prompt_tokens"), ("output_tokens", "completion_tokens"), ("total_tokens", "total_tokens")):
        value = usage.get(key)
        result[field] = value if type(value) is int and 0 <= value < 2**63 else None
    result.update(estimated_cost=None, cost_source="")
    if not settings.RESEARCH_COST_TRACKING_ENABLED:
        return result
    cost = decimal_value(usage.get("cost"))
    if cost is not None:
        result.update(estimated_cost=cost, cost_source="reported")
        return result
    for field, keys in (("prompt_tokens_details", ("cached_tokens", "cache_write_tokens", "audio_tokens", "video_tokens", "image_tokens")), ("completion_tokens_details", ("audio_tokens", "image_tokens"))):
        details = usage.get(field) or {}
        if not isinstance(details, dict) or any(details.get(key) not in (None, 0) for key in keys):
            return result
    prices = settings.OPENROUTER_MODEL_PRICING.get(model, {})
    prices = prices if isinstance(prices, dict) else {}
    rates = [decimal_value(prices.get(key)) for key in ("input_per_million", "output_per_million")]
    if all(value is not None for value in rates + [result["input_tokens"], result["output_tokens"]]):
        cost = (rates[0] * result["input_tokens"] + rates[1] * result["output_tokens"]) / Decimal(1000000)
        cost = decimal_value(cost)
        if cost is not None:
            result.update(estimated_cost=cost, cost_source="configured")
    return result


class ExecutionTimer:
    def __init__(self):
        self.started_at = timezone.now()
        self.clock = perf_counter()

    def finish(self, succeeded, raw=None, model=""):
        return {"started_at": self.started_at, "completed_at": timezone.now(),
                "latency_ms": max(0, round((perf_counter() - self.clock) * 1000)),
                "succeeded": succeeded, **usage_metrics(raw, model)}


def succeeded(response):
    return response.succeeded if response.succeeded is not None else not response.normalized_response.get("error")


def provider_key(response):
    return response.provider_key or ("consensus" if response.provider == "consensus" else response.normalized_response.get("label", response.model_name).lower())


def select_attempts(query):
    latest, active = {}, {}
    for response in query.agent_responses.order_by("pk"):
        key = provider_key(response)
        latest[key] = response
        if succeeded(response):
            active[key] = response
    return latest, active


def source_is_valid(query, source):
    if not isinstance(source, dict):
        return False
    source_id = source.get("source_id", "")
    if source.get("kind") == "academic":
        pk = source.get("citation_id")
        return type(pk) is int and 0 < pk < 2**63 and source_id == f"C{pk}" and query.citations.filter(pk=pk).exists()
    pk = source.get("agent_response_id")
    return source.get("kind") == "agent" and type(pk) is int and 0 < pk < 2**63 and source_id == f"A{pk}" and query.agent_responses.filter(pk=pk, provider="openrouter").exists()


def research_metrics(query):
    latest, active = select_attempts(query)
    attempts = list(query.agent_responses.filter(provider="openrouter")) + list(query.synthesis_attempts.all())
    costs = [item.estimated_cost for item in attempts if item.estimated_cost is not None]
    missing = len(attempts) - len(costs)
    final = getattr(query, "final_response", None)
    data = final.synthesis_data if final else {}
    registry = {s["source_id"] for s in data.get("sources", []) if source_is_valid(query, s)}
    ids = set(data.get("source_ids", []))
    for finding in data.get("key_findings", []):
        ids.update(finding.get("supporting_source_ids", []))
    judge = query.synthesis_attempts.order_by("-pk").first()
    return {
        "total_duration_ms": query.total_duration_ms,
        "interrupted_execution_metrics_incomplete": bool(query.execution_data.get("duration_incomplete")),
        "successful_providers": sum(succeeded(r) for r in latest.values()),
        "failed_providers": sum(not succeeded(r) for r in latest.values()),
        "successful_agents": sum(succeeded(r) for r in latest.values() if r.provider == "openrouter"),
        "consensus_success": succeeded(latest["consensus"]) if "consensus" in latest else None,
        "synthesis_success": judge.succeeded if judge else None,
        "paper_count": len(active["consensus"].normalized_response.get("papers", [])) if "consensus" in active else 0,
        "total_estimated_openrouter_cost": sum(costs, Decimal(0)) if attempts and not missing else None,
        "known_cost_subtotal": sum(costs, Decimal(0)), "attempts_missing_cost": missing,
        "valid_source_percent": round(100 * len(ids & registry) / len(ids), 1) if ids else None,
        "removed_source_ids": data.get("validation", {}).get("removed_source_id_count", 0),
        "agreements_count": len(data.get("agreements", [])), "disagreements_count": len(data.get("disagreements", [])),
        "evidence_strength_distribution": dict(Counter(f["evidence_strength"] for f in data.get("key_findings", []))),
        "judge_confidence": final.confidence if final else None, "answer_length": len(final.answer) if final else 0,
    }
