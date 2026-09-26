from django.conf import settings

from agents.budgets import enabled_providers, budget_for
from .execution import claim, ownership, persistence_guard
from .metrics import select_attempts
from .services import execute_plan
from .configuration import selected_judge, allowed_judges, providers_for


def recovery_plan(query, resume_synthesis=True):
    _, active = select_attempts(query)
    original = query.execution_data or {"targets": providers_for(query), "synthesis": True}
    targets = [key for key in original.get("targets", []) if key in providers_for(query) and key not in active]
    judge = selected_judge(query)
    if judge not in allowed_judges(query):
        judge = allowed_judges(query)[0]
    final = query.synthesis_attempts.filter(succeeded=True, model_name=judge).order_by("-pk").first()
    evidence_ids = sorted(r.pk for r in active.values())
    current_final = bool(final and final.synthesis_data.get("evidence_attempt_ids") == evidence_ids)
    return {"targets": targets, "synthesis": resume_synthesis and original.get("synthesis", True)
            and budget_for("synthesis")["enabled"] and (bool(targets) or not current_final), "judge_model": judge}


def recover_query(query, resume_synthesis=True, client=None, consensus_client=None, synthesis_client=None):
    plan = recovery_plan(query, resume_synthesis)
    token = claim(query, plan, mode="recovery")
    # Recheck after claiming, in case the previous owner committed before our claim.
    with ownership(query, token), persistence_guard(query):
        refreshed_plan = recovery_plan(query, resume_synthesis)
        query.execution_data.update(refreshed_plan)
        query.save(update_fields=["execution_data"])
    return execute_plan(query, token, client, consensus_client, synthesis_client)
