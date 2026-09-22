from django.core.exceptions import ImproperlyConfigured


# Balanced defaults; Claude catalog pricing and JSON-schema endpoints verified 2026-09-22.
PRODUCTION_RESEARCH_SLOTS = ("GPT", "Gemini", "Claude")
PRODUCTION_MODEL_DEFAULTS = {
    "GPT": "openai/gpt-6-astra",
    "Claude": "anthropic/claude-opus-5",
    "Gemini": "google/gemini-3.1-pro-preview",
    "synthesis": "anthropic/claude-fable-5.1",
}

BALANCED_MODEL_DEFAULTS = {
    "GPT": "openai/gpt-6-astra",
    "Gemini": "google/gemini-3.1-pro-preview",
    "Claude": "qwen/qwen3.8-max-0902",
    "synthesis": "x-ai/grok-4.20",
}

DEEP_JUDGE_DEFAULTS = {
    "gpt": ("openai/gpt-6-astra", "GPT-6 Astra", "Premium reasoning model"),
    "claude": ("anthropic/claude-fable-5.1", "Claude Fable 5.1", "Premium synthesizer — higher API cost"),
    "grok": ("x-ai/grok-4.20", "Grok 4.20", "Lower-cost deep synthesis option"),
}


def deep_judges(environment):
    default = environment.get("DEEP_DEFAULT_SYNTHESIZER", "gpt").strip().lower()
    if default not in DEEP_JUDGE_DEFAULTS:
        raise ImproperlyConfigured("DEEP_DEFAULT_SYNTHESIZER must be gpt, claude or grok.")
    choices = [{"key": key, "id": environment.get(f"DEEP_SYNTHESIZER_{key.upper()}_MODEL", "").strip() or model,
                "label": label, "warning": warning} for key, (model, label, warning) in DEEP_JUDGE_DEFAULTS.items()]
    return choices, default


def configure_production(environment):
    mode = environment.get("RESEARCH_MODE", "balanced").strip().lower()
    if mode not in {"balanced", "deep"}:
        raise ImproperlyConfigured("RESEARCH_MODE must be balanced or deep.")
    models = []
    for label in PRODUCTION_RESEARCH_SLOTS:
        if mode == "balanced":
            suffix = "THIRD" if label == "Claude" else label.upper()
            model_id = environment.get(f"BALANCED_{suffix}_MODEL", "").strip() or BALANCED_MODEL_DEFAULTS[label]
            # Preserve the existing internal retry/budget slot; use truthful UI labels.
            display = {"GPT": "GPT-6 Astra", "Gemini": "Gemini 3.1 Pro Preview", "Claude": "Qwen3.8 Max"}[label]
            if model_id != BALANCED_MODEL_DEFAULTS[label]:
                display = model_id
            models.append({"label": label, "display_label": display, "id": model_id})
        else:
            model_id = environment.get(f"DEEP_{label.upper()}_MODEL", "").strip() or production_model(environment, label)
            models.append({"label": label, "id": model_id})
    choices, default = deep_judges(environment)
    judge = (environment.get("BALANCED_SYNTHESIZER_MODEL", "").strip() or BALANCED_MODEL_DEFAULTS["synthesis"]) if mode == "balanced" else next(option["id"] for option in choices if option["key"] == default)
    return models, judge


def production_model(environment, label):
    canonical = "SYNTHESIZER_MODEL" if label == "synthesis" else f"{label.upper()}_MODEL"
    legacy = f"OPENROUTER_{label.upper()}_MODEL"
    return environment.get(canonical, "").strip() or environment.get(legacy, "").strip() or PRODUCTION_MODEL_DEFAULTS[label]


# Verified against /api/v1/models and each model's /endpoints on 2026-09-21.
# All three advertised prompt=0, completion=0 and structured_outputs/response_format.
VERIFIED_FREE_MODELS = (
    "nex-agi/nex-n2.5-pro:free",
    "dots-studio/dots-3-note-preview:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
)
VERIFIED_FREE_ROUTERS = ("openrouter/free",)


def configure_models(environment, production_models, production_judge, production_judges):
    free_mode = environment.get("OPENROUTER_FREE_TEST_MODE", "False").strip().lower() in {"true", "1", "yes"}
    models = [dict(model) for model in production_models]
    judge = production_judge
    judges = list(production_judges)
    provider_options = {"require_parameters": True}
    if free_mode:
        # Preserve the existing free-slot identities despite the paid ordering.
        free_slot_order = {"GPT": 0, "Claude": 1, "Gemini": 2}
        models.sort(key=lambda model: free_slot_order.get(model["label"], 3))
        selected = [environment.get(f"FREE_TEST_MODEL_{index}", "").strip() or default
                    for index, default in enumerate(VERIFIED_FREE_MODELS, 1)]
        judge = environment.get("FREE_TEST_SYNTHESIZER_MODEL", "").strip() or VERIFIED_FREE_MODELS[0]
        if any(model not in (*VERIFIED_FREE_MODELS, *VERIFIED_FREE_ROUTERS) for model in [*selected, judge]):
            raise ImproperlyConfigured("Free test models must come from the catalog-verified allowlist in research_ai/model_config.py.")
        for index, (model, model_id) in enumerate(zip(models, selected), 1):
            # Keep internal slot keys unchanged for budgets, retry history and recovery.
            model.update(id=model_id, display_label=f"Agent {index}")
        judges = [judge]
        provider_options["max_price"] = {"prompt": 0, "completion": 0, "request": 0}
    return {
        "OPENROUTER_FREE_TEST_MODE": free_mode,
        "OPENROUTER_MODELS": models,
        "SYNTHESIZER_MODEL": judge,
        "SYNTHESIZER_MODELS": judges,
        "OPENROUTER_PROVIDER_OPTIONS": provider_options,
    }
