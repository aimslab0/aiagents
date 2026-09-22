import re

from django.conf import settings


STOP_WORDS = {"the", "and", "for", "with", "that", "this", "does", "what", "how", "are", "from", "into", "may", "can", "was", "were", "have", "has"}


def words(text):
    return set(re.findall(r"[a-z]{3,}", text.lower())) - STOP_WORDS


def annotate_support(result, prepared):
    """Traceability and lexical-overlap flags for review, never entailment scores."""
    registry = {s["source_id"]: s for s in prepared["sources"]}
    papers = {p["source_id"]: p for p in prepared["context"]["academic_evidence"]}
    for finding in result["key_findings"]:
        ids = [sid for sid in finding["supporting_source_ids"] if sid in registry]
        academic = [sid for sid in ids if registry[sid]["kind"] == "academic"]
        agent = [sid for sid in ids if registry[sid]["kind"] == "agent"]
        claim_words = words(finding["claim"])
        matched = [sid for sid in academic if sid in papers and len(claim_words & words(papers[sid].get("abstract_or_snippet", "") + " " + papers[sid].get("takeaway", ""))) >= 2]
        declared = finding.get("declared_evidence_strength", finding["evidence_strength"])
        if not ids:
            status = "no_traceable_support"
        elif declared == "conflicting":
            status = "conflicting"
        elif not academic:
            status = "agent_supported_only"
        elif not matched or declared == "limited":
            status = "weak_support"
        else:
            status = "academically_supported"
        mismatch = declared == "strong" and (len(academic) < settings.MIN_CONSENSUS_PAPERS_FOR_STRONG_EVIDENCE or not matched)
        diagnostic_strength = "limited" if mismatch or status in {"agent_supported_only", "no_traceable_support", "weak_support"} else finding["evidence_strength"]
        finding["support_review"] = {
            "status": status, "diagnostic_evidence_strength": diagnostic_strength,
            "strong_evidence_mismatch": mismatch, "valid_academic_ids": academic, "valid_agent_ids": agent,
            "lexical_match_ids": matched,
            "note": "Diagnostic source association and simple English word overlap only; not verification of truth, study quality or entailment.",
        }
