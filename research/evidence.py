import json

from django.db import transaction

from agents.consensus import ConsensusClient
from agents.consensus_schemas import empty_consensus_response, paper_identifiers, publication_date
from agents.exceptions import ConsensusError
from agents.security import redact

from .models import AgentResponse, Citation
from .metrics import ExecutionTimer
from .execution import retry_call, persistence_guard, LeaseLost


def failed_evidence(question, error):
    result = empty_consensus_response(redact(question), redact(error.raw_response))
    result["error"] = error.as_dict()
    return result


def run_consensus(query, client=None):
    return retry_call(query, "consensus", lambda: _run_consensus(query, client))


def _run_consensus(query, client=None):
    """Retrieve one independent evidence result after the conversational models."""
    timer = ExecutionTimer()
    try:
        result = (client or ConsensusClient()).search(query.question)
    except ConsensusError as error:
        result = failed_evidence(query.question, error)
    except LeaseLost:
        raise
    except Exception:
        result = failed_evidence(query.question, ConsensusError("unexpected_error"))
    try:
        with persistence_guard(query):
            response = persist_evidence(query, result, new_attempt=True)
            for field, value in timer.finish(not result["error"]).items():
                setattr(response, field, value)
            response.paper_count = len(result.get("papers", []))
            response.save()
        return response
    except LeaseLost:
        raise
    except Exception:
        # The failed evidence transaction cannot undo earlier OpenRouter records.
        result = failed_evidence(query.question, ConsensusError("persistence", raw_response=result.get("raw_response", {})))
        with persistence_guard(query):
            response = persist_evidence(query, result, new_attempt=True)
            for field, value in timer.finish(False).items():
                setattr(response, field, value)
            response.paper_count = 0
            response.save()
    return response


@transaction.atomic
def persist_evidence(query, result, new_attempt=False):
    result = redact(result)
    values = {
            "raw_response": json.dumps(result["raw_response"], ensure_ascii=False, allow_nan=False),
            "normalized_response": result, "confidence": None,
            "provider_key": "consensus",
    }
    response = None if new_attempt else query.agent_responses.filter(provider="consensus").order_by("-pk").first()
    if response is None:
        response = AgentResponse.objects.create(research_query=query, provider="consensus", model_name="search-v1", **values)
    else:
        for field, value in values.items():
            setattr(response, field, value)
        response.save()
    if result["error"]:
        return response
    existing = list(query.citations.all())
    for paper in result["papers"]:
        identifiers = paper_identifiers(paper)
        citation = next((item for item in existing if identifiers & (
            set((item.metadata or {}).get("identifiers", []))
            | paper_identifiers({**(item.metadata or {}), "url": item.url})
        )), None)
        metadata = {**paper, "identifiers": sorted(identifiers), "provider": "consensus"}
        if citation is None:
            citation = Citation.objects.create(
                research_query=query, agent_response=response,
                title=paper["title"][:500], url=paper["url"],
                source_name=paper["journal"][:200],
                published_date=publication_date(paper["published_date"]), metadata=metadata,
            )
            existing.append(citation)
        else:
            old_metadata = citation.metadata or {}
            for key, value in metadata.items():
                if old_metadata.get(key) in (None, "", []):
                    old_metadata[key] = value
            old_metadata["identifiers"] = sorted(set(old_metadata.get("identifiers", [])) | identifiers)
            citation.metadata = old_metadata
            citation.agent_response = citation.agent_response or response
            citation.title = citation.title or paper["title"][:500]
            citation.url = citation.url or paper["url"]
            citation.source_name = citation.source_name or paper["journal"][:200]
            citation.published_date = citation.published_date or publication_date(paper["published_date"])
            citation.save()
        response.citations_used.add(citation)
    return response
