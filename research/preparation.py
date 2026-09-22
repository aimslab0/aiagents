import json

from django.conf import settings

from agents.consensus_schemas import paper_identifiers
from agents.security import redact, safe_source_url
from agents.synthesis_schemas import SynthesisError

from .claims import compare_claims
from .metrics import select_attempts
from .filtering import select_papers
from .configuration import run_models


def text(value):
    return value if isinstance(value, str) else ""


class TextBudget:
    def __init__(self, limit, source_id, notices):
        self.remaining = limit
        self.source_id = source_id
        self.notices = notices

    def take(self, value, field, limit):
        value = text(value)
        length = max(0, min(self.remaining, limit))
        excerpt = value[:length]
        self.remaining -= len(excerpt)
        if len(excerpt) < len(value):
            self.notices.append({"source_id": self.source_id, "field": field, "original_chars": len(value), "included_chars": len(excerpt)})
        return excerpt


def prepare_evidence(query):
    notices = []
    sources = []
    candidates = []
    latest, active = select_attempts(query)
    consensus = active.get("consensus")
    allowed_citations = set(consensus.citations_used.values_list("pk", flat=True)) if consensus else None
    for citation in query.citations.select_related("agent_response").order_by("pk"):
        if allowed_citations is not None and citation.pk not in allowed_citations:
            continue
        metadata = citation.metadata if isinstance(citation.metadata, dict) else {}
        if consensus:
            # Citation identity is shared; the selected attempt owns the evidence text.
            snapshot = next((paper for paper in consensus.normalized_response.get("papers", []) if paper_identifiers(paper) & paper_identifiers({**metadata, "url": citation.url})), None)
            if snapshot is not None:
                metadata = {**metadata, **snapshot}
        if metadata.get("provider") != "consensus" and (not citation.agent_response or citation.agent_response.provider != "consensus"):
            continue
        if not text(metadata.get("abstract")).strip() and not text(metadata.get("takeaway")).strip():
            notices.append({"source_id": f"C{citation.pk}", "reason": "Paper omitted: no abstract or takeaway was supplied."})
            continue
        candidates.append((citation, metadata))
    ranked, selected = select_papers(candidates, query.question, notices)
    papers = []
    for citation, metadata, priority in selected:
        source_id = f"C{citation.pk}"
        budget = TextBudget(settings.SYNTHESIS_MAX_ABSTRACT_CHARS + 5000, source_id, notices)
        authors = [budget.take(author, "author", 100) for author in (metadata.get("authors") or [])[:20]]
        if len(metadata.get("authors") or []) > 20:
            notices.append({"source_id": source_id, "reason": "Only the first twenty authors were included."})
        doi = text(metadata.get("doi"))
        if len(doi) > 300:
            notices.append({"source_id": source_id, "reason": "An overlong DOI was omitted rather than shortened."})
            doi = ""
        paper = {
            "source_id": source_id,
            "title": budget.take(metadata.get("title") or citation.title, "title", 500),
            "authors": authors,
            "year": metadata.get("year"), "journal": budget.take(metadata.get("journal") or citation.source_name, "journal", 200),
            "study_type": budget.take(metadata.get("study_type"), "study_type", 100),
            "abstract_or_snippet": budget.take(metadata.get("abstract"), "abstract", settings.SYNTHESIS_MAX_ABSTRACT_CHARS),
            "takeaway": budget.take(metadata.get("takeaway"), "takeaway", 800),
            "url": safe_source_url(citation.url), "doi": doi,
            "citation_count": metadata.get("citation_count"), "sample_size": metadata.get("sample_size"),
            "relevance_score": metadata.get("relevance_score"),
            "quality_metadata": {key: (metadata.get("api_metadata") or {}).get(key) for key in ("is_preprint", "sjr_best_quartile")},
            "priority": priority,
        }
        papers.append(paper)
        sources.append({
            "source_id": source_id, "kind": "academic", "citation_id": citation.pk,
            "title": paper["title"], "source_name": paper["journal"], "year": paper["year"],
            "url": paper["url"], "doi": paper["doi"],
        })

    agents = []
    seen_models = set()
    available = [response for response in active.values() if response.provider == "openrouter"]
    for response in available:
        data = response.normalized_response
        if not isinstance(data, dict) or data.get("error") or response.model_name in seen_models:
            continue
        if not text(data.get("answer")).strip() and not data.get("key_findings"):
            continue
        seen_models.add(response.model_name)
        source_id = f"A{response.pk}"
        limit = settings.SYNTHESIS_MAX_AGENT_CHARS
        budget = TextBudget(limit, source_id, notices)
        answer = budget.take(data.get("answer"), "answer", limit * 3 // 5)
        findings = [budget.take(finding, "key_findings", min(400, limit // 20)) for finding in (data.get("key_findings") or [])[:8]]
        if len(data.get("key_findings") or []) > 8:
            notices.append({"source_id": source_id, "reason": "Only the first eight agent findings were included."})
        citations = []
        for item in (data.get("citations") or [])[:5]:
            url = safe_source_url(item.get("url", ""))
            fitted_url = budget.take(url, "citation_url", len(url))
            citations.append({
                "title": budget.take(item.get("title"), "citation_title", 200),
                "source_name": budget.take(item.get("source_name"), "citation_source", 100),
                "url": url if fitted_url == url else "",
            })
        if len(data.get("citations") or []) > 5:
            notices.append({"source_id": source_id, "reason": "Only the first five unverified agent citations were included."})
        if not answer.strip() and not any(finding.strip() for finding in findings):
            notices.append({"source_id": source_id, "reason": "Agent omitted: context limit left no usable answer or finding."})
            continue
        linked = [paper["source_id"] for paper in papers if any(paper_identifiers(paper) & paper_identifiers(item) for item in citations if item["url"])]
        score = min(len(citations), 5) * settings.SYNTHESIS_SCORE_WEIGHTS["agent_citation"] + min(len(linked), 3) * settings.SYNTHESIS_SCORE_WEIGHTS["agent_academic_link"]
        agents.append({
            "source_id": source_id, "model": response.model_name, "answer": answer,
            "key_findings": [item for item in findings if item.strip()],
            "confidence": response.confidence, "citations": citations,
            "cited_academic_source_ids": linked, "priority_score": score,
        })
    agents.sort(key=lambda item: (-item["priority_score"], item["source_id"]))
    omitted_agents = max(0, len(agents) - settings.SYNTHESIS_MAX_AGENTS)
    agents = agents[:settings.SYNTHESIS_MAX_AGENTS]
    for agent in agents:
        sources.append({"source_id": agent["source_id"], "kind": "agent", "agent_response_id": int(agent["source_id"][1:]), "title": f"{agent['model']} research response", "source_name": "AI model output (unverified)", "year": None, "url": "", "doi": ""})
    context = {
        "question": query.question,
        "agent_findings": agents, "academic_evidence": papers,
        "claim_analysis": compare_claims(agents, papers),
        "context_limits": {
            "truncations_and_omissions": notices,
            "omitted_paper_count": len(ranked) - len(selected), "omitted_agent_count": omitted_agents,
            "academic_evidence_available": bool(papers),
            "missing_agent_models": [model["id"] for model in run_models(query) if model["id"] not in {agent["model"] for agent in agents}],
        },
        "scoring_note": "Priority heuristics organize context only. Agent agreement and confidence never determine the answer. A linked citation is not proof of claim support.",
    }
    warnings = []
    if len(sources) < settings.MIN_VALID_SOURCES_FOR_SYNTHESIS:
        warnings.append("Fewer traceable sources than the configured review threshold; interpretation must be provisional.")
    if len(agents) < settings.MIN_SUCCESSFUL_AGENTS:
        warnings.append("Fewer successful agents than the configured review threshold; independent perspectives are limited.")
    if len(papers) < settings.MIN_CONSENSUS_PAPERS_FOR_STRONG_EVIDENCE:
        warnings.append("Academic paper count is below the configured strong-evidence review threshold.")
    context["evidence_thresholds"] = {"minimum_valid_sources": settings.MIN_VALID_SOURCES_FOR_SYNTHESIS,
        "minimum_successful_agents": settings.MIN_SUCCESSFUL_AGENTS,
        "minimum_academic_papers_for_strong": settings.MIN_CONSENSUS_PAPERS_FOR_STRONG_EVIDENCE,
        "warnings": warnings, "policy": "Review thresholds are advisory; usable evidence may still be synthesized."}
    context, sources = redact(context), redact(sources)
    if len(json.dumps(context, ensure_ascii=False, allow_nan=False)) > settings.SYNTHESIS_MAX_CONTEXT_CHARS:
        raise SynthesisError("context_limit")
    return {"context": context, "sources": sources, "usable": bool(agents or papers)}
