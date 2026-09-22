"""Read-only formatting of stored research for the dashboard."""
from agents.consensus_schemas import paper_identifiers
from agents.security import safe_source_url


def prepare_dashboard_result(query, active, registry):
    data = query.final_result.synthesis_data if query.final_result else {}
    findings = data.get("key_findings", [])
    strengths = {finding.get("evidence_strength") for finding in findings}
    query.conflicting_findings = sum(f.get("evidence_strength") == "conflicting" for f in findings)
    for finding in findings:
        review = finding.get("support_review") or {}
        finding["support_label"] = review.get("status", "").replace("_", " ")
    query.evidence_label = next((name.title() for name in ("conflicting", "limited", "moderate", "strong") if name in strengths), "Not assessed")
    query.valid_source_count = len(registry)
    query.active_agent_count = sum(r.provider == "openrouter" for r in active.values())
    query.consensus_available = "consensus" in active
    query.ui_singleton = [query]
    consensus = active.get("consensus")
    query.ranked_papers = []
    if not consensus:
        return
    citations = list(consensus.citations_used.all())
    priorities = {p["source_id"]: p.get("total", 0) for p in data.get("diagnostics", {}).get("paper_priorities", [])}
    for original in consensus.normalized_response.get("papers", []):
        paper = {**original, "url": safe_source_url(original.get("url", ""))}
        citation = next((c for c in citations if paper_identifiers(paper) & paper_identifiers({**(c.metadata or {}), "url": c.url})), None)
        paper["source_id"] = f"C{citation.pk}" if citation else ""
        paper["stored_priority"] = priorities.get(paper["source_id"], -1)
        query.ranked_papers.append(paper)
    query.ranked_papers.sort(key=lambda p: p["stored_priority"], reverse=True)
