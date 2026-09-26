import json

from django.conf import settings
from django.utils import timezone

from agents.exceptions import AgentError
from agents.openrouter import OpenRouterClient, redact
from agents.schemas import empty_response

from .models import AgentResponse, ResearchQuery
from .evidence import run_consensus
from .synthesis import synthesize_research
from .metrics import ExecutionTimer, select_attempts, succeeded
from agents.budgets import budget_for, enabled_providers
from .execution import claim, ownership, touch, persistence_guard, retry_call, ResearchBusy, LeaseLost
from .configuration import run_models, selected_judge, allowed_judges, is_plan_and_solve, providers_for


def run_model(client, question, model_id):
    """One independent call; the runner can later schedule these concurrently."""
    try:
        return client.research(question, model_id)
    except AgentError as error:
        result = empty_response(model_id, redact(error.raw_response))
        result["error"] = error.as_dict()
        return result
    except Exception:
        # Do not persist or log arbitrary upstream exception text or request headers.
        result = empty_response(model_id)
        result["error"] = AgentError("unexpected_error").as_dict()
        return result


def run_research(query, client=None, consensus_client=None, synthesis_client=None):
    plan = {"targets": providers_for(query), "synthesis": budget_for("synthesis")["enabled"], "judge_model": selected_judge(query)}
    token = claim(query, plan)
    if token is None:
        query.refresh_from_db()
        return query
    return execute_plan(query, token, client, consensus_client, synthesis_client)


def run_agent(query, model, client=None):
    return retry_call(query, model["label"].lower(), lambda: _run_agent(query, model, client))


def _run_agent(query, model, client=None):
    timer = ExecutionTimer()
    result = run_model(client or OpenRouterClient(provider_key=model["label"].lower(), planner=is_plan_and_solve(query)), query.question, model["id"])
    result["label"] = model.get("display_label", model["label"])
    with persistence_guard(query):
        return AgentResponse.objects.create(
            research_query=query, provider="openrouter", provider_key=model["label"].lower(), model_name=model["id"],
            raw_response=json.dumps(result["raw_response"], ensure_ascii=False, allow_nan=False),
            normalized_response=result, confidence=result["confidence"],
            **timer.finish(not result["error"], result["raw_response"], model["id"]),
        )


def execute_plan(query, token, client=None, consensus_client=None, synthesis_client=None):
    timer = ExecutionTimer()
    plan = query.execution_data
    models = {m["label"].lower(): m for m in run_models(query)}
    with ownership(query, token):
        try:
            for key in plan.get("targets", []):
                if key not in providers_for(query):
                    continue
                touch(query, "COLLECTING_CONSENSUS" if key == "consensus" else "COLLECTING_AGENTS", key)
                if is_plan_and_solve(query) and key in {"consensus", "semantic_scholar"}:
                    from .planning import save_plan
                    from .hybrid import retrieve
                    save_plan(query)
                    retrieve(query, key, consensus_client if key == "consensus" else None)
                elif key == "consensus":
                    run_consensus(query, client=consensus_client)
                elif key in models:
                    run_agent(query, models[key], client)
            if is_plan_and_solve(query):
                from .planning import save_plan
                from .hybrid import prepare_hybrid
                save_plan(query)
                prepare_hybrid(query)
            if plan.get("synthesis") and budget_for("synthesis")["enabled"]:
                touch(query, "PREPARING_EVIDENCE", "synthesis")
                synthesize_research(query, client=synthesis_client, model_id=selected_judge(query))
            finish_execution(query, timer)
        except LeaseLost:
            # Another recovery owns this run; this runner cannot commit or continue.
            pass
        except Exception:
            # Keep unfinished work discoverable without returning an exception body.
            ResearchQuery.objects.filter(pk=query.pk, execution_token=token).update(
                lease_expires_at=timezone.now(), execution_data={**query.execution_data, "error": "Execution interrupted; recovery is available."})
    query.refresh_from_db()
    return query


def finish_execution(query, timer):
    with persistence_guard(query):
        latest, active = select_attempts(query)
        required = providers_for(query)
        success = any(key in active for key in required)
        all_success = all(key in latest and succeeded(latest[key]) and not latest[key].normalized_response.get('partial') for key in required)
        final = query.synthesis_attempts.order_by("-pk").first()
        judge_ok = not budget_for("synthesis")["enabled"] or bool(final and final.succeeded and final.synthesis_data.get("evidence_attempt_ids") == sorted(r.pk for r in active.values()))
        query.status = "completed" if success else "failed"
        query.stage = "COMPLETED" if success and all_success and judge_ok else "PARTIAL" if success else "FAILED"
        query.completed_at = timezone.now()
        query.total_duration_ms = (query.total_duration_ms or 0) + timer.finish(True)["latency_ms"]
        query.lease_expires_at = None
        query.execution_data = {**query.execution_data, "current_provider": ""}
        query.save(update_fields=["status", "stage", "completed_at", "total_duration_ms", "lease_expires_at", "execution_data"])


def retry_research(query, target, model_id=None, client=None, consensus_client=None, synthesis_client=None, action_key=None):
    models = {model["label"].lower(): model for model in run_models(query)}
    if target not in {*models, "consensus", "synthesis", "failed", *(['semantic_scholar'] if is_plan_and_solve(query) else [])}:
        raise ValueError("Unknown retry target.")
    if model_id and (target != "synthesis" or model_id not in allowed_judges(query)):
        raise ValueError("Judge model is not configured.")
    if target != "failed" and not budget_for(target)["enabled"]:
        raise ValueError("Provider is disabled.")
    latest, _ = select_attempts(query)
    targets = [key for key in providers_for(query) if key not in latest or not succeeded(latest[key]) or latest[key].normalized_response.get('partial')] if target == "failed" else [] if target == "synthesis" else [target]
    plan = {"targets": targets, "synthesis": target == "synthesis", "judge_model": model_id or selected_judge(query)}
    token = claim(query, plan, mode="retry", action_key=action_key, action_target=target, model_id=model_id or "")
    if token is None:
        query.refresh_from_db()
        return query
    return execute_plan(query, token, client, consensus_client, synthesis_client)
