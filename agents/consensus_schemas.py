import hashlib
import json
import re
from datetime import date
from urllib.parse import unquote, urlsplit, urlunsplit

from jsonschema import Draft202012Validator

from .exceptions import ConsensusError
from .security import safe_source_url

# Field names follow the official GET /v1/search QueryResult contract.
STRING_FIELDS = (
    "title", "abstract", "doi", "journal_name", "pages", "url", "volume",
    "study_type", "takeaway", "publisher_name", "population_type", "publish_date",
)
INTEGER_FIELDS = (
    "publish_year", "citation_count", "sjr_best_quartile", "sample_size",
    "study_count", "study_duration_days", "influential_citation_count",
)
ARRAY_FIELDS = ("authors", "full_text_chunks", "countries_of_study", "institutions")
PAPER_PROPERTIES = {
    **{name: {"type": ["string", "null"]} for name in STRING_FIELDS},
    **{name: {"type": ["integer", "null"]} for name in INTEGER_FIELDS},
    **{name: {"type": ["array", "null"], "items": {"type": "string"}} for name in ARRAY_FIELDS},
    "semantic_score": {"type": ["number", "null"]},
    "is_preprint": {"type": ["boolean", "null"]},
}
SEARCH_VALIDATOR = Draft202012Validator({
    "type": "object",
    "required": ["results"],
    "properties": {
        "results": {"type": "array", "items": {
            "type": "object", "required": ["title"], "properties": PAPER_PROPERTIES,
        }},
        "page": {"type": "integer", "minimum": 0},
        "page_size": {"type": "integer", "minimum": 1},
        "is_end": {"type": "boolean"},
        "next_page": {"type": ["integer", "null"], "minimum": 0},
    },
})


def empty_consensus_response(question, raw_response=None):
    return {
        "provider": "consensus", "query": question, "summary": "",
        "papers": [], "key_findings": [], "error": None,
        "raw_response": raw_response or {}, "pagination": {},
    }


def publication_date(value):
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    return None


def canonical_doi(value):
    if not isinstance(value, str):
        return ""
    value = unquote(value.strip())
    value = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value, flags=re.I)
    return value.casefold() if re.fullmatch(r"10\.\d{4,9}/\S+", value) else ""


def paper_identifiers(paper):
    keys = set()
    doi = canonical_doi(paper.get("doi"))
    url = safe_source_url(paper.get("url", ""))
    if url:
        parts = urlsplit(url)
        keys.add("url:" + urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, "")))
        if parts.hostname in {"doi.org", "dx.doi.org"}:
            doi = doi or canonical_doi(url)
    if doi:
        keys.add("doi:" + doi)
    if not keys:
        # Exact record equality is a fallback; title similarity alone never merges papers.
        encoded = json.dumps(paper, sort_keys=True, ensure_ascii=False).encode("utf-8")
        keys.add("record:" + hashlib.sha256(encoded).hexdigest())
    return keys


def normalize_search(body, question):
    if not SEARCH_VALIDATOR.is_valid(body):
        raise ConsensusError("malformed_response", raw_response=body)
    result = empty_consensus_response(question, body)
    seen = {}
    for source in body["results"]:
        if not isinstance(source.get("title"), str) or not source["title"].strip():
            raise ConsensusError("malformed_response", raw_response=body)
        paper = {
            "title": source["title"], "authors": source.get("authors") or [],
            "year": source.get("publish_year"), "journal": source.get("journal_name") or "",
            "abstract": source.get("abstract") or "", "url": safe_source_url(source.get("url")),
            "doi": source.get("doi") or "", "citation_count": source.get("citation_count"),
            "study_type": source.get("study_type") or "", "sample_size": source.get("sample_size"),
            "relevance_score": source.get("semantic_score"),
            "published_date": source.get("publish_date") or "", "takeaway": source.get("takeaway") or "",
            "api_metadata": {key: value for key, value in source.items() if key in PAPER_PROPERTIES and key != "url"},
        }
        identities = paper_identifiers(paper)
        existing = next((seen[key] for key in identities if key in seen), None)
        if existing is None:
            result["papers"].append(paper)
            existing = paper
        else:
            for key, value in paper.items():
                if existing.get(key) in (None, "", []):
                    existing[key] = value
            for key, value in paper["api_metadata"].items():
                if existing["api_metadata"].get(key) in (None, "", []):
                    existing["api_metadata"][key] = value
        for key in identities:
            seen[key] = existing
        if paper["takeaway"] and paper["takeaway"] not in result["key_findings"]:
            result["key_findings"].append(paper["takeaway"])
    result["pagination"] = {key: body[key] for key in ("page", "page_size", "is_end", "next_page") if key in body}
    return result
