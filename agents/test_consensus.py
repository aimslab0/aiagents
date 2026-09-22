import json
from unittest.mock import Mock, patch

import requests
from django.test import SimpleTestCase, override_settings

from .consensus import API_URL, ConsensusClient
from .consensus_schemas import normalize_search, publication_date
from .exceptions import ConsensusError


def paper_fixture(**overrides):
    paper = {
        "title": "Example review of sleep and memory", "authors": ["A. Example", "B. Example"],
        "publish_year": 2025, "publish_date": "2025-03-12", "journal_name": "Example Journal",
        "abstract": "This is a synthetic test abstract, not a real research claim.",
        "url": "https://example.org/papers/sleep", "doi": "10.1234/example.sleep",
        "citation_count": 0, "study_type": "systematic review", "sample_size": 120,
        "semantic_score": 0.91, "takeaway": "An illustrative provider takeaway.",
        "sjr_best_quartile": 1, "is_preprint": False, "study_count": 8,
        "population_type": "human", "publisher_name": "Example Publisher",
    }
    paper.update(overrides)
    return paper


def search_fixture(papers=None, **overrides):
    body = {"results": [paper_fixture()] if papers is None else papers, "page": 0, "page_size": 20, "is_end": True, "next_page": None}
    body.update(overrides)
    return body


def search_response(body=None, status=200):
    return Mock(status_code=status, text=json.dumps(search_fixture() if body is None else body))


@override_settings(CONSENSUS_API_KEY="consensus-test-secret", OPENROUTER_API_KEY="openrouter-test-secret",
                   CONSENSUS_PAGE_SIZE=20, CONSENSUS_YEAR_MIN=None, CONSENSUS_STUDY_TYPES=[],
                   CONSENSUS_CONNECT_TIMEOUT=5, CONSENSUS_READ_TIMEOUT=30)
class ConsensusClientTests(SimpleTestCase):
    def setUp(self):
        mocked_http = patch("agents.consensus.requests.get")
        self.http = mocked_http.start()
        self.addCleanup(mocked_http.stop)
        self.http.return_value = search_response()
        self.client = ConsensusClient()

    def test_success_uses_documented_contract(self):
        result = self.client.search("Does sleep support memory?")
        args, kwargs = self.http.call_args
        self.assertEqual(args, (API_URL,))
        self.assertEqual(kwargs["headers"], {"x-api-key": "consensus-test-secret"})
        self.assertEqual(kwargs["params"], {"query": "Does sleep support memory?", "page": 0, "page_size": 20, "exclude_preprints": "true", "include_semantic_score": "true"})
        self.assertEqual(kwargs["timeout"], (5, 30))
        self.assertFalse(kwargs["allow_redirects"])
        self.assertEqual(result["provider"], "consensus")
        self.assertEqual(result["summary"], "")
        self.assertEqual(result["papers"][0]["year"], 2025)
        self.assertEqual(result["papers"][0]["journal"], "Example Journal")
        self.assertEqual(result["papers"][0]["relevance_score"], 0.91)
        self.assertEqual(result["papers"][0]["citation_count"], 0)
        self.assertEqual(result["key_findings"], [paper_fixture()["takeaway"]])
        self.assertIsNone(result["error"])
        self.http.return_value.close.assert_called_once()

    def test_no_papers_is_successful_search(self):
        self.http.return_value = search_response(search_fixture([]))
        result = self.client.search("Question")
        self.assertEqual(result["papers"], [])
        self.assertEqual(result["key_findings"], [])
        self.assertIsNone(result["error"])

    def test_missing_metadata_is_not_invented(self):
        self.http.return_value = search_response(search_fixture([{"title": "A returned title"}]))
        paper = self.client.search("Question")["papers"][0]
        for key in ("doi", "journal", "abstract", "url", "study_type", "published_date"):
            self.assertEqual(paper[key], "")
        for key in ("year", "citation_count", "relevance_score", "sample_size"):
            self.assertIsNone(paper[key])
        self.assertEqual(paper["authors"], [])

    def test_null_metadata_is_accepted(self):
        self.http.return_value = search_response(search_fixture([paper_fixture(authors=None, doi=None, semantic_score=None, abstract=None, journal_name=None)]))
        self.assertEqual(self.client.search("Question")["papers"][0]["authors"], [])

    def test_malformed_responses(self):
        for value in ["invalid JSON", "null", "[]", "{}", '{"results": null}', '{"results": [null]}', '{"results": [{}]}', json.dumps(search_fixture([paper_fixture(authors="not an array")])), json.dumps(search_fixture([paper_fixture(citation_count=True)])), '{"results": [], "page": NaN}']:
            with self.subTest(value=value):
                self.http.return_value = Mock(status_code=200, text=value)
                with self.assertRaises(ConsensusError) as error:
                    self.client.search("Question")
                self.assertEqual(error.exception.code, "malformed_response")

    def test_timeout_and_connection_failure(self):
        for exception, code in [(requests.Timeout("consensus-test-secret"), "timeout"), (requests.ConnectionError("consensus-test-secret"), "connection")]:
            self.http.side_effect = exception
            with self.assertRaises(ConsensusError) as error:
                self.client.search("Question")
            self.assertEqual(error.exception.code, code)
            self.assertNotIn("consensus-test-secret", str(error.exception))

    def test_authentication_and_other_api_errors_are_safe(self):
        for status, code in [(401, "authentication"), (403, "authentication"), (402, "billing"), (429, "rate_limit"), (500, "api_error"), (302, "api_error")]:
            self.http.return_value = search_response({"detail": "consensus-test-secret openrouter-test-secret", "x-api-key": "unexpected secret"}, status=status)
            with self.assertRaises(ConsensusError) as error:
                self.client.search("Question")
            self.assertEqual(error.exception.code, code)
            for secret in ["consensus-test-secret", "openrouter-test-secret", "unexpected secret"]:
                self.assertNotIn(secret, str(error.exception))
                self.assertNotIn(secret, json.dumps(error.exception.raw_response))

    def test_success_body_credentials_are_redacted(self):
        self.http.return_value = search_response(search_fixture([paper_fixture(abstract="consensus-test-secret openrouter-test-secret")]))
        result = self.client.search("Question")
        self.assertNotIn("consensus-test-secret", json.dumps(result))
        self.assertNotIn("openrouter-test-secret", json.dumps(result))

    @override_settings(CONSENSUS_API_KEY="")
    def test_missing_key_makes_no_request(self):
        with self.assertRaises(ConsensusError) as error:
            self.client.search("Question")
        self.assertEqual(error.exception.code, "configuration")
        self.http.assert_not_called()

    @override_settings(CONSENSUS_YEAR_MIN=2020, CONSENSUS_STUDY_TYPES=["systematic review", "meta-analysis"])
    def test_optional_supported_filters(self):
        self.client.search("Question")
        params = self.http.call_args.kwargs["params"]
        self.assertEqual(params["year_min"], 2020)
        prepared = requests.Request("GET", API_URL, params=params).prepare()
        self.assertIn("study_types=systematic+review&study_types=meta-analysis", prepared.url)

    @override_settings(CONSENSUS_STUDY_TYPES=["invented filter"])
    def test_invalid_study_type_does_not_reach_api(self):
        with self.assertRaises(ConsensusError):
            self.client.search("Question")
        self.http.assert_not_called()

    def test_untrusted_urls_are_removed(self):
        for url in ["javascript:alert(1)", "data:text/html,hi", "//example.org", "http://127.0.0.1/a", "http://[::1]/a", "https://localhost/a", "https://name:password@example.org/a", "https://example.org/\nattack"]:
            with self.subTest(url=url):
                self.http.return_value = search_response(search_fixture([paper_fixture(url=url)]))
                self.assertEqual(self.client.search("Question")["papers"][0]["url"], "")

    def test_deduplication_by_doi_and_url_preserves_order(self):
        papers = [paper_fixture(), paper_fixture(doi="https://doi.org/10.1234/EXAMPLE.SLEEP", url="https://example.org/other"), paper_fixture(doi="", url="https://example.org/papers/sleep#abstract")]
        result = normalize_search(search_fixture(papers), "Question")
        self.assertEqual(len(result["papers"]), 1)
        self.assertEqual(len(result["raw_response"]["results"]), 3)

    def test_equal_titles_with_different_identifiers_are_not_merged(self):
        result = normalize_search(search_fixture([paper_fixture(), paper_fixture(doi="10.1234/another", url="https://example.org/another")]), "Question")
        self.assertEqual(len(result["papers"]), 2)

    def test_duplicate_enriches_missing_metadata_without_losing_zero(self):
        result = normalize_search(search_fixture([
            paper_fixture(sample_size=None, sjr_best_quartile=None),
            paper_fixture(citation_count=15),
        ]), "Question")
        self.assertEqual(len(result["papers"]), 1)
        paper = result["papers"][0]
        self.assertEqual(paper["sample_size"], 120)
        self.assertEqual(paper["api_metadata"]["sjr_best_quartile"], 1)
        self.assertEqual(paper["citation_count"], 0)

    def test_publication_dates_do_not_invent_a_day(self):
        for value in ["2025", "2025-03", "2025-02-30", None]:
            self.assertIsNone(publication_date(value))
        self.assertEqual(publication_date("2025-03-12").isoformat(), "2025-03-12")
