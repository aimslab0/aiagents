"""Topic-discovery prompts and an adapter to the existing research response contract."""
import json
import re

from jsonschema import Draft202012Validator

from .exceptions import AgentError
from .schemas import RESEARCH_SCHEMA, SYSTEM_PROMPT, normalize_content, strict_json_loads


def is_topic_discovery(question):
    text = question.lower()
    return bool(re.search(r"\b(?:thesis|dissertation|research)[ -]topic(?:s| ideas)?\b|\btopic (?:ideas|discovery|ideation)\b", text)
                or re.search(r"\btopics?\b.{0,100}\b(?:thesis|dissertation|master['’]?s)\b", text)
                or re.search(r"\b(?:suggest|propose|find|identify|generate|recommend|explore|discover|choose|select)\b.{0,100}\b(?:research questions?|research gaps?)\b", text))


SHORT_TEXT = {"type": "string", "maxLength": 350}
SHORT_LIST = {"type": "array", "maxItems": 6, "items": SHORT_TEXT}
IDEA_PROPERTIES = {name: SHORT_TEXT for name in (
    "title", "research_gap", "why_it_matters", "population", "possible_research_question",
    "methodology_idea", "feasibility",
)}
IDEA_PROPERTIES.update(main_variables=SHORT_LIST, evidence_needed=SHORT_LIST)
TOPIC_SCHEMA = {
    "type": "object",
    "properties": {
        "problem_area": SHORT_TEXT,
        "topic_ideas": {"type": "array", "minItems": 1, "maxItems": 3, "items": {
            "type": "object", "properties": IDEA_PROPERTIES,
            "required": list(IDEA_PROPERTIES), "additionalProperties": False,
        }},
        "key_findings": SHORT_LIST, "uncertainties": SHORT_LIST,
        "citations": RESEARCH_SCHEMA["properties"]["citations"],
        "confidence": RESEARCH_SCHEMA["properties"]["confidence"],
    },
    "required": ["problem_area", "topic_ideas", "key_findings", "uncertainties", "citations", "confidence"],
    "additionalProperties": False,
}
TOPIC_VALIDATOR = Draft202012Validator(TOPIC_SCHEMA)
TOPIC_PROMPT = SYSTEM_PROMPT.split("Return ONLY a JSON object", 1)[0] + """
Help discover feasible Master's-level thesis topics, particularly in health and
education. Identify important current problems, what is well studied versus unclear,
recent trends you can reliably describe, and possible research gaps. Treat each gap
as a hypothesis needing a literature review, not an established absence of research.
Do not invent gaps because few or no papers were found. You have no live retrieval.
Return at most three specific, distinct topic ideas using the supplied JSON schema.
For each, state its importance, population, main variables, possible research question,
methodological options (and a testable hypothesis where appropriate), practical
feasibility (access, time, ethics, resources), and evidence needed to assess novelty.
Prefer feasible observational, survey, qualitative or secondary-data approaches when
appropriate; do not assume access to clinical trials or sensitive records. Distinguish
association from causation. Avoid saturated topics without a specific defensible gap.
Use key_findings for established knowledge and uncertainties for unverified gaps and
missing context. Keep every field concise, ideally under 120 characters. Return JSON
only. Confidence is a 0-100 estimate or null, not a probability; citations must not be
fabricated. Use empty citation URLs when the exact URL is unknown.
"""

TOPIC_JUDGE_PROMPT = """
TOPIC DISCOVERY: Compare the agents' thesis ideas against supplied Consensus academic
evidence. Prioritize relevant recent literature and available study metadata, while
retaining older foundational evidence where appropriate. Do not assume this search
is exhaustive or up to date. Distinguish under-researched from not found in current
evidence; few returned papers never establish a genuine gap. Avoid saturated topics
unless a specific population, variable, setting or methodological gap is supported.
Recommend at most three promising, feasible Master's-level topics. Use final_answer
for a concise ranked recommendation, executive_summary for the main rationale, and
at most three key_findings for topic-specific recommendations. For each, include why
it is promising, cautious evidence/gap assessment, a possible research question,
population, variables, method and feasibility in claim/explanation. Clearly state
limitations and unverified novelty. Prefer supplied Consensus sources over model
speculation and cite only allowed_source_ids. Preserve the existing output schema.
"""


def normalize_topic_content(content, model_id, raw_response):
    try:
        parsed = strict_json_loads(content)
        if not TOPIC_VALIDATOR.is_valid(parsed) or not parsed["problem_area"].strip():
            raise ValueError
        if any(not idea["title"].strip() or not idea["possible_research_question"].strip() for idea in parsed["topic_ideas"]):
            raise ValueError
    except (ValueError, TypeError, RecursionError):
        raise AgentError("malformed_response", raw_response=raw_response) from None
    # Preserve full structured ideas in JSONField; bounded prose keeps the existing
    # evidence-preparation and dashboard contracts useful without changing them.
    brief = [parsed["problem_area"]]
    for idea in parsed["topic_ideas"]:
        brief.append("\n" + idea["title"][:120])
        for key in ("research_gap", "why_it_matters", "population", "possible_research_question", "methodology_idea", "feasibility"):
            brief.append(f"{key.replace('_', ' ').title()}: {idea[key][:120]}")
        brief.append("Variables: " + "; ".join(idea["main_variables"])[:120])
        brief.append("Evidence needed: " + "; ".join(idea["evidence_needed"])[:120])
    brief.append("Uncertainties: " + "; ".join(parsed["uncertainties"])[:250])
    generic = {"answer": "\n".join(brief), "key_findings": parsed["key_findings"],
               "evidence": parsed["uncertainties"], "citations": parsed["citations"], "confidence": parsed["confidence"]}
    result = normalize_content(json.dumps(generic), model_id, raw_response)
    result.update({key: parsed[key] for key in ("problem_area", "topic_ideas", "uncertainties")})
    return result
