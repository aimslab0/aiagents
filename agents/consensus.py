import requests
from django.conf import settings
from django.views.decorators.debug import sensitive_variables

from .consensus_schemas import normalize_search
from .exceptions import ConsensusError
from .schemas import strict_json_loads
from .security import redact
from .budgets import budget_for

API_URL = "https://api.consensus.app/v1/search"
STUDY_TYPES = {
    "bench experiment", "case report", "case study", "case-control study", "cohort study",
    "commentary or perspective", "cross-sectional study", "field study", "historical or archival analysis",
    "interview study", "literature review", "longitudinal / panel data study", "meta-analysis",
    "mixed methods study", "non-randomized experimental study", "non-rct in vitro", "other", "rct",
    "systematic review", "theoretical, modeling, or simulation study", "non-rct experimental",
    "non-rct observational study", "animal",
}


class ConsensusClient:
    def __init__(self, page_size=None):
        self.page_size = page_size if page_size is not None else settings.CONSENSUS_PAGE_SIZE

    @sensitive_variables()
    def search(self, research_question):
        if not budget_for("consensus")["enabled"] or not settings.CONSENSUS_API_KEY or not set(settings.CONSENSUS_STUDY_TYPES) <= STUDY_TYPES:
            raise ConsensusError("configuration")
        params = {
            "query": research_question, "page": 0, "page_size": self.page_size,
            "exclude_preprints": "true", "include_semantic_score": "true",
        }
        if settings.CONSENSUS_YEAR_MIN is not None:
            params["year_min"] = settings.CONSENSUS_YEAR_MIN
        if settings.CONSENSUS_STUDY_TYPES:
            params["study_types"] = settings.CONSENSUS_STUDY_TYPES
        try:
            response = requests.get(
                API_URL, params=params, headers={"x-api-key": settings.CONSENSUS_API_KEY},
                timeout=(settings.CONSENSUS_CONNECT_TIMEOUT, budget_for("consensus")["timeout"]),
                allow_redirects=False,
            )
        except requests.Timeout:
            raise ConsensusError("timeout") from None
        except requests.RequestException:
            raise ConsensusError("connection") from None
        try:
            body = redact(strict_json_loads(response.text))
        except (ValueError, TypeError, RecursionError):
            body = {"body": redact(response.text)}
        finally:
            response.close()
        raw = body if isinstance(body, dict) else {"body": body}
        if not 200 <= response.status_code < 300:
            code = {401: "authentication", 403: "authentication", 402: "billing", 429: "rate_limit", 408: "timeout", 504: "timeout"}.get(response.status_code, "api_error")
            raise ConsensusError(code, raw_response=raw, status=response.status_code)
        return normalize_search(raw, redact(research_question))
