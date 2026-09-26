import os
import math
import json
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv
from .model_config import configure_models, configure_production, deep_judges, ALTERNATIVE_SYNTHESIZER_DEFAULT, BALANCED_MODEL_DEFAULTS

BASE_DIR = Path(__file__).resolve().parent.parent
# Process environment wins; secrets containing ${...} remain literal values.
load_dotenv(BASE_DIR / ".env", override=False, interpolate=False)

SECRET_KEY = os.getenv("SECRET_KEY", "").strip()
if not SECRET_KEY:
    raise ImproperlyConfigured("Set SECRET_KEY in your environment or .env file.")

DEBUG = os.getenv("DEBUG", "False").strip().lower() in {"true", "1", "yes"}
ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]").split(",")
    if host.strip()
]
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "chats.apps.ChatsConfig",
    "research.apps.ResearchConfig",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "research_ai.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]
WSGI_APPLICATION = "research_ai.wsgi.application"
DATABASES = {"default": {
    "ENGINE": "django.db.backends.sqlite3",
    "NAME": BASE_DIR / "db.sqlite3",
}}
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
TEST_RUNNER = 'research_ai.test_runner.OfflineTestRunner'
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
X_FRAME_OPTIONS = "DENY"

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
PROMPT_ENHANCER_MODEL = os.getenv("PROMPT_ENHANCER_MODEL", "").strip() or "google/gemini-2.5-flash"
PRIMARY_SYNTHESIZER_MODEL = os.getenv("PRIMARY_SYNTHESIZER_MODEL", "").strip() or BALANCED_MODEL_DEFAULTS['synthesis']
ALTERNATIVE_SYNTHESIZER_MODEL = os.getenv("ALTERNATIVE_SYNTHESIZER_MODEL", "").strip() or ALTERNATIVE_SYNTHESIZER_DEFAULT
SEMANTIC_SCHOLAR_API_KEY = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "").strip()
SEMANTIC_SCHOLAR_ENABLED = os.getenv("SEMANTIC_SCHOLAR_ENABLED", "True").lower() in {"true", "1", "yes"}
SEMANTIC_SCHOLAR_TIMEOUT = float(os.getenv("SEMANTIC_SCHOLAR_TIMEOUT", "30"))
SEMANTIC_SCHOLAR_MAX_RETRIES = int(os.getenv("SEMANTIC_SCHOLAR_MAX_RETRIES", "0"))
MAX_RETRIEVAL_QUERIES = int(os.getenv("MAX_RETRIEVAL_QUERIES", "5"))
SEMANTIC_SCHOLAR_RESULTS_PER_QUERY = int(os.getenv("SEMANTIC_SCHOLAR_RESULTS_PER_QUERY", "5"))
CONSENSUS_RESULTS_PER_QUERY = int(os.getenv("CONSENSUS_RESULTS_PER_QUERY", "5"))
SYNTHESIS_MAX_EVIDENCE_ITEMS = int(os.getenv("SYNTHESIS_MAX_EVIDENCE_ITEMS", "8"))
PLANNER_MAX_TOKENS = int(os.getenv("PLANNER_MAX_TOKENS", "1600"))
if not math.isfinite(SEMANTIC_SCHOLAR_TIMEOUT) or SEMANTIC_SCHOLAR_TIMEOUT <= 0 or not 0 <= SEMANTIC_SCHOLAR_MAX_RETRIES <= 5:
    raise ImproperlyConfigured("Invalid Semantic Scholar timeout or retry count.")
if not 1 <= MAX_RETRIEVAL_QUERIES <= 5 or not 1 <= SEMANTIC_SCHOLAR_RESULTS_PER_QUERY <= 100 or not 1 <= CONSENSUS_RESULTS_PER_QUERY <= 20 or not 1 <= SYNTHESIS_MAX_EVIDENCE_ITEMS <= 30 or PLANNER_MAX_TOKENS <= 0:
    raise ImproperlyConfigured("Invalid planning or retrieval limits.")
OPENROUTER_MODELS, SYNTHESIZER_MODEL = configure_production(os.environ)
RESEARCH_MODE = os.getenv("RESEARCH_MODE", "balanced").strip().lower()
DEEP_SYNTHESIZER_OPTIONS, DEEP_DEFAULT_SYNTHESIZER = deep_judges(os.environ)
DEEP_SYNTHESIS_BUDGETS = {}
for judge_key, prefix, defaults in (
    ('deepseek', 'DEEPSEEK', (8, 1800, 40000)),
    ('claude', 'CLAUDE', (20, 3000, 100000)),
):
    values = {key: int(os.getenv(f'{prefix}_SYNTHESIS_{suffix}', str(default)))
              for key, suffix, default in zip(('items', 'abstract_chars', 'context_chars'),
              ('MAX_EVIDENCE_ITEMS', 'MAX_ABSTRACT_CHARS', 'MAX_CONTEXT_CHARS'), defaults)}
    if not 1 <= values['items'] <= 50 or not 100 <= values['abstract_chars'] <= 10000 or not 5000 <= values['context_chars'] <= 200000:
        raise ImproperlyConfigured('Invalid Deep synthesis evidence/context budget.')
    DEEP_SYNTHESIS_BUDGETS[judge_key] = values
RESEARCH_PROFILES = {mode: configure_production({**os.environ, "RESEARCH_MODE": mode}) for mode in ("balanced", "deep")}
OPENROUTER_PRODUCTION_MODELS = [dict(model) for model in OPENROUTER_MODELS]
OPENROUTER_CONNECT_TIMEOUT = float(os.getenv("OPENROUTER_CONNECT_TIMEOUT", "5"))
OPENROUTER_READ_TIMEOUT = float(os.getenv("OPENROUTER_READ_TIMEOUT", "120"))
OPENROUTER_MAX_TOKENS = int(os.getenv("OPENROUTER_MAX_TOKENS", "4096"))

CONSENSUS_API_KEY = os.getenv("CONSENSUS_API_KEY", "").strip()
CONSENSUS_CONNECT_TIMEOUT = float(os.getenv("CONSENSUS_CONNECT_TIMEOUT", "5"))
CONSENSUS_READ_TIMEOUT = float(os.getenv("CONSENSUS_READ_TIMEOUT", "30"))
CONSENSUS_PAGE_SIZE = int(os.getenv("CONSENSUS_PAGE_SIZE", "20"))
CONSENSUS_YEAR_MIN = int(os.environ["CONSENSUS_YEAR_MIN"]) if os.getenv("CONSENSUS_YEAR_MIN", "").strip() else None
CONSENSUS_STUDY_TYPES = [value.strip() for value in os.getenv("CONSENSUS_STUDY_TYPES", "").split(",") if value.strip()]
if CONSENSUS_PAGE_SIZE < 1 or CONSENSUS_CONNECT_TIMEOUT <= 0 or CONSENSUS_READ_TIMEOUT <= 0:
    raise ImproperlyConfigured("Consensus page size and timeouts must be positive.")

SYNTHESIZER_TIMEOUT = float(os.getenv("SYNTHESIZER_TIMEOUT", "180"))
SYNTHESIZER_MAX_TOKENS = int(os.getenv("SYNTHESIZER_MAX_TOKENS", "6000"))
SYNTHESIS_MAX_AGENT_CHARS = int(os.getenv("SYNTHESIS_MAX_AGENT_CHARS", "6000"))
SYNTHESIS_MAX_AGENTS = int(os.getenv("SYNTHESIS_MAX_AGENTS", "3"))
SYNTHESIS_MAX_PAPERS = int(os.getenv("SYNTHESIS_MAX_PAPERS", "8"))
SYNTHESIS_MAX_ABSTRACT_CHARS = int(os.getenv("SYNTHESIS_MAX_ABSTRACT_CHARS", "2000"))
SYNTHESIS_MAX_CONTEXT_CHARS = int(os.getenv("SYNTHESIS_MAX_CONTEXT_CHARS", "80000"))
SYNTHESIS_CLAIM_SIMILARITY = float(os.getenv("SYNTHESIS_CLAIM_SIMILARITY", "0.8"))
SYNTHESIS_USE_RECENCY = os.getenv("SYNTHESIS_USE_RECENCY", "False").lower() in {"true", "1", "yes"}
SYNTHESIS_SCORE_WEIGHTS = {
    name: float(os.getenv(f"SYNTHESIS_WEIGHT_{name.upper()}", str(default)))
    for name, default in {
        "relevance": 40, "review": 15, "peer_review": 5, "journal_quartile": 5,
        "citations": 3, "sample_size": 2, "recency": 3,
        "agent_citation": 0.5, "agent_academic_link": 2,
    }.items()
}
if not math.isfinite(SYNTHESIZER_TIMEOUT) or min(SYNTHESIZER_TIMEOUT, SYNTHESIZER_MAX_TOKENS, SYNTHESIS_MAX_AGENT_CHARS, SYNTHESIS_MAX_AGENTS,
       SYNTHESIS_MAX_PAPERS, SYNTHESIS_MAX_ABSTRACT_CHARS, SYNTHESIS_MAX_CONTEXT_CHARS) <= 0:
    raise ImproperlyConfigured("Synthesis timeouts and context limits must be positive.")
if not 0 < SYNTHESIS_CLAIM_SIMILARITY <= 1 or any(not math.isfinite(weight) or weight < 0 for weight in SYNTHESIS_SCORE_WEIGHTS.values()):
    raise ImproperlyConfigured("Synthesis weights must be nonnegative and claim similarity must be in (0, 1].")

LOGGING = {
    "version": 1, "disable_existing_loggers": False,
    "formatters": {"safe": {
        "()": "research_ai.logging.SafeConsoleFormatter",
        "format": "{asctime} {levelname} {name}: {message}", "style": "{",
    }},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "safe"}},
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {
        "django": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "django.server": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "research.synthesis": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}

RESEARCH_DIAGNOSTICS_ENABLED = os.getenv("RESEARCH_DIAGNOSTICS_ENABLED", "True").lower() in {"true", "1", "yes"}
RESEARCH_COST_TRACKING_ENABLED = os.getenv("RESEARCH_COST_TRACKING_ENABLED", "True").lower() in {"true", "1", "yes"}
SYNTHESIZER_MODELS = list(dict.fromkeys([SYNTHESIZER_MODEL] + [m.strip() for m in os.getenv("SYNTHESIZER_MODELS", "").split(",") if m.strip()]))
PRODUCTION_SYNTHESIZER_MODEL = SYNTHESIZER_MODEL
PRODUCTION_SYNTHESIZER_MODELS = list(SYNTHESIZER_MODELS)
globals().update(configure_models(os.environ, OPENROUTER_PRODUCTION_MODELS, PRODUCTION_SYNTHESIZER_MODEL, PRODUCTION_SYNTHESIZER_MODELS))
try:
    OPENROUTER_MODEL_PRICING = json.loads(os.getenv("OPENROUTER_MODEL_PRICING", "{}"))
    if not isinstance(OPENROUTER_MODEL_PRICING, dict):
        raise ValueError
except (ValueError, TypeError):
    raise ImproperlyConfigured("OPENROUTER_MODEL_PRICING must be a JSON object.") from None

# Zero automatic retries preserves an explicit spending opt-in for existing installs.
OPENROUTER_MAX_RETRIES = int(os.getenv("OPENROUTER_MAX_RETRIES", "0"))
CONSENSUS_MAX_RETRIES = int(os.getenv("CONSENSUS_MAX_RETRIES", "0"))
PROVIDER_RETRY_BACKOFF = float(os.getenv("PROVIDER_RETRY_BACKOFF", "2"))
PROVIDER_BUDGETS = {}
for provider_name in ("gpt", "claude", "gemini", "consensus", "synthesis"):
    prefix = provider_name.upper()
    budget = {"enabled": os.getenv(f"{prefix}_ENABLED", "True").lower() in {"true", "1", "yes"}}
    for suffix, field, convert in (("TIMEOUT", "timeout", float), ("MAX_TOKENS", "max_tokens", int), ("MAX_RETRIES", "max_retries", int), ("RETRY_BACKOFF", "backoff", float)):
        if os.getenv(f"{prefix}_{suffix}", "").strip():
            budget[field] = convert(os.environ[f"{prefix}_{suffix}"])
    PROVIDER_BUDGETS[provider_name] = budget
RESEARCH_STALE_AFTER_SECONDS = int(os.getenv("RESEARCH_STALE_AFTER_SECONDS", "900"))
MIN_CONSENSUS_PAPERS_FOR_STRONG_EVIDENCE = int(os.getenv("MIN_CONSENSUS_PAPERS_FOR_STRONG_EVIDENCE", "2") or "2")
MIN_VALID_SOURCES_FOR_SYNTHESIS = int(os.getenv("MIN_VALID_SOURCES_FOR_SYNTHESIS", "1") or "1")
MIN_SUCCESSFUL_AGENTS = int(os.getenv("MIN_SUCCESSFUL_AGENTS", "1") or "1")
if not 0 <= OPENROUTER_MAX_RETRIES <= 5 or not 0 <= CONSENSUS_MAX_RETRIES <= 5 or not 0 <= PROVIDER_RETRY_BACKOFF <= 30:
    raise ImproperlyConfigured("Retry counts must be 0-5 and backoff must be 0-30 seconds.")
if RESEARCH_STALE_AFTER_SECONDS < 60 or min(MIN_CONSENSUS_PAPERS_FOR_STRONG_EVIDENCE, MIN_VALID_SOURCES_FOR_SYNTHESIS, MIN_SUCCESSFUL_AGENTS) < 0:
    raise ImproperlyConfigured("Stale interval must be at least 60 seconds and evidence thresholds nonnegative.")
for budget in PROVIDER_BUDGETS.values():
    if any(not math.isfinite(value) or value <= 0 for key, value in budget.items() if key in {"timeout", "max_tokens"}):
        raise ImproperlyConfigured("Provider timeouts and token limits must be positive and finite.")
    if not 0 <= budget.get("max_retries", 0) <= 5 or not 0 <= budget.get("backoff", 0) <= 30:
        raise ImproperlyConfigured("Provider retry counts must be 0-5 and backoff 0-30 seconds.")
LOGGING["loggers"]["research.execution"] = {"handlers": ["console"], "level": "INFO", "propagate": False}
LOGGING["loggers"]["agents.prompt_enhancer"] = {"handlers": ["console"], "level": "INFO", "propagate": False}
