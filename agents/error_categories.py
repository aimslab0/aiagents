TRANSIENT = {"timeout", "rate_limit", "provider_unavailable", "network_error"}


def classify(code, status=None):
    if status in (401, 403):
        return "authentication"
    if status == 429:
        return "rate_limit"
    if status in (408, 504):
        return "timeout"
    if type(status) is int and 500 <= status <= 599:
        return "provider_unavailable"
    if type(status) is int and 400 <= status <= 499:
        return "invalid_request"
    return {"timeout": "timeout", "authentication": "authentication", "rate_limit": "rate_limit",
            "connection": "network_error", "malformed_response": "malformed_response",
            "truncated_response": "malformed_response", "untraceable_source": "malformed_response",
            "configuration": "invalid_request", "credits": "invalid_request", "billing": "invalid_request",
            "refusal": "invalid_request", "context_limit": "invalid_request", "insufficient_evidence": "invalid_request"}.get(code, "unknown")
