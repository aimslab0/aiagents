from django.conf import settings


def provider_for_model(model_id):
    return next((m["label"].lower() for m in settings.OPENROUTER_MODELS if m["id"] == model_id), "gpt")


def budget_for(provider):
    consensus = provider == "consensus"
    synthesis = provider == "synthesis"
    return {
        "enabled": True,
        "timeout": settings.CONSENSUS_READ_TIMEOUT if consensus else settings.SYNTHESIZER_TIMEOUT if synthesis else settings.OPENROUTER_READ_TIMEOUT,
        "max_tokens": None if consensus else settings.SYNTHESIZER_MAX_TOKENS if synthesis else settings.OPENROUTER_MAX_TOKENS,
        "max_retries": settings.CONSENSUS_MAX_RETRIES if consensus else settings.OPENROUTER_MAX_RETRIES,
        "backoff": settings.PROVIDER_RETRY_BACKOFF,
        **settings.PROVIDER_BUDGETS.get(provider, {}),
    }


def enabled_providers():
    keys = [m["label"].lower() for m in settings.OPENROUTER_MODELS] + ["consensus"]
    return [key for key in keys if budget_for(key)["enabled"]]
