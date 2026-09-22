import logging

from django.conf import settings
from django.db import DatabaseError
from django.utils import timezone

from agents.synthesizer import SynthesizerClient
from agents.synthesis_schemas import SynthesisError, normalize_synthesis
from agents.security import redact

from .models import FinalResponse, SynthesisAttempt
from .metrics import ExecutionTimer, select_attempts
from .preparation import prepare_evidence
from .execution import retry_call, persistence_guard, touch, LeaseLost
from .support import annotate_support
from .configuration import selected_judge, allowed_judges

logger = logging.getLogger(__name__)


def synthesize_research(query, client=None, model_id=None):
    return retry_call(query, "synthesis", lambda: _synthesize_research(query, client, model_id))


def _synthesize_research(query, client=None, model_id=None):
    model_id = model_id or selected_judge(query)
    if model_id not in allowed_judges(query):
        raise ValueError("Judge model is not configured.")
    timer = ExecutionTimer()
    client = client or SynthesizerClient(model_id)
    evidence_ids = sorted(r.pk for r in select_attempts(query)[1].values())
    prepared = None
    rejected_ids = []
    stage = "evidence_preparation"
    logger.info("Synthesis start query_id=%s model=%s", query.pk, redact(model_id))
    try:
        touch(query, "PREPARING_EVIDENCE", "synthesis")
        prepared = prepare_evidence(query)
        context = prepared["context"]
        logger.info("Synthesis evidence query_id=%s agents=%s papers=%s", query.pk, len(context["agent_findings"]), len(context["academic_evidence"]))
        if not prepared["usable"]:
            raise SynthesisError("insufficient_evidence")
        touch(query, "SYNTHESIZING", "synthesis")
        stage = "provider_request"
        content = client.synthesize(context)
        stage = "structured_response_validation"
        result = normalize_synthesis(content, prepared["sources"])
        stage = "support_annotation"
        annotate_support(result, prepared)
        if not context["academic_evidence"]:
            result["limitations"].append("No usable academic evidence was available; this interpretation relies on unverified AI findings.")
        result.update({
            "status": "completed", "error": None, "model": model_id,
            "evidence_attempt_ids": evidence_ids,
            "generated_at": timezone.now().isoformat(),
            "sources": prepared["sources"],
            "diagnostics": {
                "claim_analysis": context["claim_analysis"], "context_limits": context["context_limits"],
                "paper_priorities": [{"source_id": paper["source_id"], **paper["priority"]} for paper in context["academic_evidence"]],
                "score_weights": settings.SYNTHESIS_SCORE_WEIGHTS, "recency_enabled": settings.SYNTHESIS_USE_RECENCY,
                "evidence_thresholds": context.get("evidence_thresholds", {}),
            },
        })
        stage = "persistence"
        with persistence_guard(query):
            attempt = SynthesisAttempt.objects.create(research_query=query, model_name=model_id, answer=result["final_answer"], confidence=result["overall_confidence"], synthesis_data=result, **timer.finish(True, getattr(client, "raw_metadata", {}), model_id))
            final, _ = FinalResponse.objects.update_or_create(
                research_query=query,
                defaults={"answer": result["final_answer"], "confidence": result["overall_confidence"], "synthesis_data": result, "active_attempt": attempt},
            )
        logger.info("Synthesis completion query_id=%s model=%s status=completed attempt_id=%s sources=%s", query.pk, redact(model_id), attempt.pk, len(result["source_ids"]))
        return final
    except LeaseLost:
        raise
    except SynthesisError as error:
        safe_error = error.as_dict()
        rejected_ids = error.rejected_source_ids
        if error.code == "untraceable_source":
            stage = "source_validation"
        elif error.code in {"malformed_response", "truncated_response"}:
            stage = "json_parsing"
    except DatabaseError:
        safe_error = SynthesisError("persistence").as_dict()
        stage = "persistence"
    except Exception:
        safe_error = SynthesisError("unexpected_error").as_dict()
    logger.warning("Synthesis failure query_id=%s model=%s stage=%s code=%s category=%s http_status=%s rejected_source_count=%s", query.pk, redact(model_id), stage, safe_error["code"], safe_error["category"], safe_error.get("http_status"), len(rejected_ids))
    failure = {
        "status": "insufficient_evidence" if safe_error["code"] == "insufficient_evidence" else "failed",
        "error": safe_error, "model": model_id,
        "failure_stage": stage,
        "attempted_at": timezone.now().isoformat(),
        "sources": prepared["sources"] if prepared else [],
        "validation": {"rejected_source_ids": rejected_ids, "removed_source_id_count": 0},
    }
    try:
        with persistence_guard(query):
            attempt = SynthesisAttempt.objects.create(research_query=query, model_name=model_id, synthesis_data=failure, **timer.finish(False, getattr(client, "raw_metadata", {}), model_id))
            previous = FinalResponse.objects.filter(research_query=query).first()
            if previous and previous.answer and previous.synthesis_data.get("status") == "completed":
                previous.synthesis_data["last_attempt_error"] = failure
                previous.save(update_fields=["synthesis_data"])
                final = previous
            else:
                final, _ = FinalResponse.objects.update_or_create(
                    research_query=query, defaults={"answer": "", "confidence": None, "synthesis_data": failure},
                )
    except DatabaseError:
        logger.error("Synthesis failure query_id=%s model=%s stage=persistence code=failure_record_not_saved", query.pk, redact(model_id))
        raise
    logger.info("Synthesis completion query_id=%s model=%s status=failed attempt_id=%s", query.pk, redact(model_id), attempt.pk)
    return final
