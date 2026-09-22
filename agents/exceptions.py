from .error_categories import classify

ERROR_MESSAGES = {
    "configuration": "OpenRouter is not configured. Set OPENROUTER_API_KEY on the server.",
    "timeout": "This model timed out. Other model results are still available.",
    "connection": "Could not reach OpenRouter. Please try again later.",
    "authentication": "OpenRouter rejected the server credentials. Check the API key.",
    "credits": "The OpenRouter account has insufficient credits.",
    "rate_limit": "OpenRouter rate-limited this request. Please try again later.",
    "api_error": "OpenRouter could not complete this model request.",
    "malformed_response": "The model did not return a valid structured research response.",
    "truncated_response": "The model reached its output limit before finishing the response.",
    "refusal": "The model declined to answer this question.",
    "unexpected_error": "This model request could not be completed.",
}


class AgentError(Exception):
    """Only static, user-safe messages; never include upstream exception text."""

    def __init__(self, code, *, raw_response=None, status=None):
        self.code = code if code in ERROR_MESSAGES else "unexpected_error"
        self.raw_response = raw_response if raw_response is not None else {}
        self.category = classify(self.code, status)
        super().__init__(ERROR_MESSAGES[self.code])

    def as_dict(self):
        return {"code": self.code, "message": str(self), "category": self.category}


class ConsensusError(Exception):
    messages = {
        "configuration": "Consensus is not configured. Check the server API key and search settings.",
        "authentication": "Consensus rejected the server credentials. Check the API key.",
        "billing": "Consensus search is unavailable because of an account billing issue.",
        "rate_limit": "Consensus search reached a rate or usage limit. Please try again later.",
        "timeout": "Consensus search timed out. Other provider results are preserved.",
        "connection": "Could not reach Consensus. Please try again later.",
        "api_error": "Consensus could not complete this academic search.",
        "malformed_response": "Consensus returned an invalid academic search response.",
        "unexpected_error": "Academic evidence could not be retrieved. Other provider results are preserved.",
        "persistence": "Academic evidence could not be saved. Other provider results are preserved.",
    }

    def __init__(self, code, *, raw_response=None, status=None):
        self.code = code if code in self.messages else "unexpected_error"
        self.raw_response = raw_response if raw_response is not None else {}
        self.category = classify(self.code, status)
        super().__init__(self.messages[self.code])

    def as_dict(self):
        return {"code": self.code, "message": str(self), "category": self.category}
