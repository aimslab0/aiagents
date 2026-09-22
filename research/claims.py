import re

from django.conf import settings

STOP_WORDS = {"a", "an", "the", "is", "are", "was", "were", "do", "does", "did", "can", "may", "to", "of", "in", "on", "for", "with", "and", "that", "it", "not", "no", "never"}


def signature(text):
    text = text.lower().replace("n't", " not")
    words = re.findall(r"[a-z0-9]+", text)
    negative = bool({"not", "no", "never"} & set(words))
    tokens = {word[:-1] if len(word) > 4 and word.endswith("s") else word for word in words if word not in STOP_WORDS}
    return tokens, negative


def relationship(left, right):
    a, negative_a = signature(left)
    b, negative_b = signature(right)
    if min(len(a), len(b)) < 3:
        return None
    overlap = len(a & b) / len(a | b)
    if overlap < settings.SYNTHESIS_CLAIM_SIMILARITY:
        return None
    return "potential_conflict" if negative_a != negative_b else "potential_support"


def compare_claims(agents, papers):
    claims = []
    for agent in agents:
        findings = agent["key_findings"] or re.split(r"(?<=[.!?])\s+", agent["answer"])[:3]
        for finding in findings:
            if finding.strip():
                claims.append({"claim": finding, "source_id": agent["source_id"], "model": agent["model"], "supporting_agents": [], "contradicting_agents": [], "academic_matches": []})
    for claim in claims:
        for other in claims:
            if other["model"] == claim["model"]:
                continue
            match = relationship(claim["claim"], other["claim"])
            key = "supporting_agents" if match == "potential_support" else "contradicting_agents"
            if match and other["source_id"] not in claim[key]:
                claim[key].append(other["source_id"])
        for paper in papers:
            passages = [paper["takeaway"]] + re.split(r"(?<=[.!?])\s+", paper["abstract_or_snippet"])
            matches = {relationship(claim["claim"], passage) for passage in passages}
            for match in ("potential_support", "potential_conflict"):
                if match in matches:
                    claim["academic_matches"].append({"source_id": paper["source_id"], "relationship": match})
        claim["external_evidence_status"] = "candidate_matches_require_review" if claim["academic_matches"] else "no_text_match"
        claim["independent_agent_support_count"] = 1 + len(claim["supporting_agents"])
    return {
        "method": "Conservative lexical overlap and simple negation; matches are not verified entailment. Missing matches do not prove absence of support.",
        "claims": claims,
        "unresolved_disagreements": [{"claim": claim["claim"], "source_id": claim["source_id"], "other_agent_ids": claim["contradicting_agents"]} for claim in claims if claim["contradicting_agents"]],
    }
