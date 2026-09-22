"""Resolve server-owned run configuration without accepting browser model IDs."""
from django.conf import settings


def select_configuration(mode, judge_key=None):
    if mode not in {"balanced", "deep"}:
        raise ValueError("Invalid research mode.")
    options = {option["key"]: option for option in settings.DEEP_SYNTHESIZER_OPTIONS}
    if judge_key and judge_key not in options:
        raise ValueError("Invalid synthesizer selection.")
    if settings.OPENROUTER_FREE_TEST_MODE:
        return {"mode": mode, "models": settings.OPENROUTER_MODELS, "judge_model": settings.SYNTHESIZER_MODEL, "judge_key": "", "free_test": True}
    models, judge = settings.RESEARCH_PROFILES[mode]
    key = judge_key or settings.DEEP_DEFAULT_SYNTHESIZER
    if mode == "deep":
        judge = options[key]["id"]
    return {"mode": mode, "models": models, "judge_model": judge, "judge_key": key if mode == "deep" else "", "free_test": False}


def run_models(query):
    selection = query.execution_data.get("selection") or {}
    if settings.OPENROUTER_FREE_TEST_MODE or selection.get("free_test"):
        # Never resume a free run using newly enabled paid model configuration.
        return selection.get("models", settings.OPENROUTER_MODELS) if not settings.OPENROUTER_FREE_TEST_MODE else settings.OPENROUTER_MODELS
    return selection.get("models", settings.OPENROUTER_MODELS)


def allowed_judges(query):
    selection = query.execution_data.get("selection") or {}
    if settings.OPENROUTER_FREE_TEST_MODE:
        return settings.SYNTHESIZER_MODELS
    if selection.get("free_test"):
        return [selection["judge_model"]]
    if selection.get("mode") == "deep":
        return [option["id"] for option in settings.DEEP_SYNTHESIZER_OPTIONS]
    if selection.get("mode") == "balanced":
        return [selection["judge_model"]]
    return settings.SYNTHESIZER_MODELS


def selected_judge(query):
    if settings.OPENROUTER_FREE_TEST_MODE:
        return settings.SYNTHESIZER_MODEL
    selection = query.execution_data.get("selection") or {}
    return query.execution_data.get("judge_model") or selection.get("judge_model") or settings.SYNTHESIZER_MODEL
