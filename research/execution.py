import json
import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timedelta
from uuid import uuid4

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from agents.budgets import budget_for
from agents.error_categories import TRANSIENT, classify
from agents.security import redact
from .models import ResearchQuery, ResearchAction

logger = logging.getLogger(__name__)
owner = ContextVar("research_execution_owner", default=None)


class ResearchBusy(Exception):
    pass


class LeaseLost(Exception):
    pass


def lease_deadline():
    longest = max(budget_for(key)["timeout"] for key in ("gpt", "claude", "gemini", "consensus", "semantic_scholar", "synthesis"))
    return timezone.now() + timedelta(seconds=max(settings.RESEARCH_STALE_AFTER_SECONDS, longest + max(settings.OPENROUTER_CONNECT_TIMEOUT, settings.CONSENSUS_CONNECT_TIMEOUT) + 60))


def stale_queries():
    now = timezone.now()
    cutoff = now - timedelta(seconds=settings.RESEARCH_STALE_AFTER_SECONDS)
    return ResearchQuery.objects.filter(status="processing").filter(
        Q(lease_expires_at__lt=now) | Q(lease_expires_at=None, started_at__lt=cutoff)
        | Q(lease_expires_at=None, started_at=None, created_at__lt=cutoff)
    )


@transaction.atomic
def claim(query, plan, mode="initial", action_key=None, action_target="", model_id=""):
    now = timezone.now()
    token = uuid4()
    if action_key:
        action = ResearchAction.objects.filter(key=action_key).first()
        if action:
            if (action.research_query_id, action.target, action.model_name) != (query.pk, action_target, model_id):
                raise ValueError("Action token was already used for another request.")
            return None
    available = stale_queries().filter(pk=query.pk) if mode == "recovery" else ResearchQuery.objects.filter(pk=query.pk)
    if mode == "initial":
        available = available.filter(status="pending")
    elif mode == "retry":
        available = available.exclude(status="processing")
    data = {**plan, "current_provider": "", "recovered": mode == "recovery" or query.execution_data.get("recovered", False),
            "duration_incomplete": mode == "recovery" or query.execution_data.get("duration_incomplete", False),
            "claimed_at": now.isoformat()}
    if query.execution_data.get("selection") is not None:
        data["selection"] = query.execution_data["selection"]
    for field in ('research_plan', 'hybrid_diagnostics', 'merged_evidence'):
        if field in query.execution_data:
            data[field] = query.execution_data[field]
    if not available.update(status="processing", stage="COLLECTING_AGENTS", execution_token=token,
                            lease_expires_at=lease_deadline(), execution_data=data, completed_at=None):
        if mode == "initial":
            return None
        raise ResearchBusy
    if action_key:
        ResearchAction.objects.create(key=action_key, research_query=query, target=action_target, model_name=model_id)
    if not query.started_at:
        ResearchQuery.objects.filter(pk=query.pk).update(started_at=now)
    query.refresh_from_db()
    return token


@contextmanager
def ownership(query, token):
    reset = owner.set((query.pk, token))
    try:
        yield
    finally:
        owner.reset(reset)


def touch(query, stage=None, provider=None):
    lease = owner.get()
    if not lease or lease[0] != query.pk:
        return
    values = {"lease_expires_at": lease_deadline()}
    if stage:
        values["stage"] = stage
    if provider is not None:
        query.execution_data = {**query.execution_data, "current_provider": provider}
        values["execution_data"] = query.execution_data
    if not ResearchQuery.objects.filter(pk=query.pk, status="processing", execution_token=lease[1], lease_expires_at__gt=timezone.now()).update(**values):
        raise LeaseLost


@contextmanager
def persistence_guard(query):
    # A conditional write fences old runners before they commit provider results.
    with transaction.atomic():
        touch(query)
        yield


def event(query, provider, record):
    data = record.synthesis_data if provider == "synthesis" else record.normalized_response
    error = data.get("error") or {}
    number = query.synthesis_attempts.count() if provider == "synthesis" else query.agent_responses.filter(provider_key=provider).count()
    logger.info(json.dumps(redact({"query_id": query.pk, "provider": provider, "model": record.model_name,
        "attempt_number": number, "status": "failed" if error else "succeeded", "latency_ms": record.latency_ms,
        "error_category": error.get("category", classify(error.get("code"))) if error else None})))


def retry_call(query, provider, call):
    budget = budget_for(provider)
    if not budget["enabled"]:
        return None
    result = None
    for number in range(budget["max_retries"] + 1):
        touch(query, provider=provider)
        result = call()
        record = query.synthesis_attempts.order_by("-pk").first() if provider == "synthesis" else result
        if record is None:
            return result
        event(query, provider, record)
        data = record.synthesis_data if provider == "synthesis" else record.normalized_response
        error = data.get("error") or {}
        if not error or error.get("category", classify(error.get("code"))) not in TRANSIENT or number == budget["max_retries"]:
            break
        touch(query)
        time.sleep(min(30, budget["backoff"] * 2**number))
    return result
