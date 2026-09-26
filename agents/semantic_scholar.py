"""Academic Graph relevance search. Optional key; no scraping or implicit retries."""
import requests
from django.conf import settings
from django.views.decorators.debug import sensitive_variables
from .exceptions import AgentError
from .schemas import strict_json_loads
from .security import redact

API_URL = 'https://api.semanticscholar.org/graph/v1/paper/search'
FIELDS = 'paperId,title,abstract,year,authors,venue,url,externalIds,citationCount,influentialCitationCount,publicationTypes,fieldsOfStudy,openAccessPdf,isOpenAccess'


class SemanticScholarError(AgentError):
    def as_dict(self):
        value = super().as_dict()
        value['message'] = f"Semantic Scholar retrieval failed ({value['code']})."
        return value


class SemanticScholarClient:
    @sensitive_variables()
    def search(self, query):
        headers = {'x-api-key': settings.SEMANTIC_SCHOLAR_API_KEY} if settings.SEMANTIC_SCHOLAR_API_KEY else {}
        try:
            response = requests.get(API_URL, params={'query': query, 'limit': settings.SEMANTIC_SCHOLAR_RESULTS_PER_QUERY, 'fields': FIELDS},
                                    headers=headers, timeout=(5, settings.SEMANTIC_SCHOLAR_TIMEOUT), allow_redirects=False)
        except requests.Timeout:
            raise SemanticScholarError('timeout') from None
        except requests.RequestException:
            raise SemanticScholarError('connection') from None
        try:
            if not 200 <= response.status_code < 300:
                code = {401: 'authentication', 403: 'authentication', 429: 'rate_limit', 408: 'timeout', 504: 'timeout'}.get(response.status_code, 'api_error')
                raise SemanticScholarError(code, status=response.status_code)
            body = redact(strict_json_loads(response.text))
            if not isinstance(body, dict) or not isinstance(body.get('data'), list):
                raise ValueError
            from research.academic import normalize_semantic
            return {'papers': [normalize_semantic(p) for p in body['data'][:settings.SEMANTIC_SCHOLAR_RESULTS_PER_QUERY]], 'raw_response': body}
        except (ValueError, TypeError, KeyError, RecursionError):
            raise SemanticScholarError('malformed_response') from None
        finally:
            response.close()
