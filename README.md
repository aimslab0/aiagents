# Multi AI Research

## Research dashboard

When deploying updated templates, run `python manage.py collectstatic --noinput`
and refresh cached pages so the browser receives matching JavaScript and CSS.
The chat dialogs need the current `dashboard.js`; missing or stale scripts leave
Rename/Delete/Clear All without their click handlers. `theme.js` must also return
JavaScript successfully. With `DEBUG=False`, Django's normal development server
does not serve static files. For local development use `DEBUG=True` (paid/free
research selection is independent of DEBUG); production must keep DEBUG disabled
and serve `/static/` from STATIC_ROOT using the host's static-file configuration.

Synthesis errors now show their safe reason in the snapshot and diagnostics, with
model, failure stage, category and HTTP status in terminal logs where available.
Excluded-paper IDs are kept in stored diagnostics but withheld from the judge's
omission notices, and the request states the allowed citation IDs explicitly.
Validation still rejects unsupported citations. Existing failed runs are not modified;
use Re-run Synthesis to retry saved evidence, which makes one billable judge call in
paid mode. Provider outputs and Consensus evidence are reused.

The dashboard presents each saved question in three reading levels: a compact final
answer and evidence snapshot, key findings and the best available academic papers,
then **View Detailed Research** for complete model responses, Consensus evidence,
judge details, retry controls, attempt history, evaluations and developer diagnostics.
The first five findings and three papers are shown initially; remaining entries and
full answer/abstract text expand on demand. Disagreements and limitations stay visible.
Paper order uses stored synthesis priorities when available, otherwise the original
Consensus order. No scoring or synthesis runs during dashboard rendering.

The evidence-strength summary shows the lowest reported finding strength, with
conflicts taking precedence; it is a display summary, not a new evidence score.
Confidence is the synthesizer's estimate, not a statistical probability. Counts of
successful agents and papers describe active saved results, which can predate a failed
retry; diagnostics retain the complete attempt record. External source links are
sanitized and open with `noopener noreferrer`.

Sidebar controls support New Research, local title search, rename, and deletion of an
individual chat. Deletion confirms the selected chat ID and cascades through existing
database relationships. Clear All Chats requires typing `DELETE ALL CHATS`. Mutations
are POST-only and CSRF-protected. These controls never invoke research APIs.

Light, Dark and System appearance persist in localStorage. System follows the device
color scheme without reloading. Search, confirmation dialogs, theme persistence and
copy actions require JavaScript; copy requires browser clipboard permission and a
secure context (localhost is supported). Native details remain keyboard-accessible.
Export is a future placeholder, not an implemented action. This redesign adds no
migrations, model changes or research configuration changes.

A single-user Django research workspace. Step 4 sends each question to three
OpenRouter models, retrieves academic evidence from Consensus, prepares bounded
evidence, and generates a final answer with a separate OpenRouter judge. Responses,
citations, and the final synthesis are stored in SQLite. Celery, Redis, parallel
execution, and background workers are not implemented.

## Local setup (Windows PowerShell)

Use Python 3.10 or newer (Python 3.13 is recommended for this project).

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

Put the generated key in `.env` as `SECRET_KEY='your-generated-key'`. Keep the quotes
so special characters are parsed correctly. Never commit `.env`. Set
`OPENROUTER_API_KEY` and `CONSENSUS_API_KEY` to your respective provider keys.
If `.env` already exists, preserve it instead of copying over it.
For local development, set `DEBUG=True` and
`ALLOWED_HOSTS=localhost,127.0.0.1,[::1]` in `.env`; the example file starts with
production-safe `DEBUG=False` and an empty host list for you to fill in.

For an existing Step 3 installation, the judge reuses `OPENROUTER_API_KEY`. No new
secret, dependency, or migration is needed. Optional synthesis settings are below.

```powershell
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py runserver
```

Open http://127.0.0.1:8000/. There is no dashboard login. Django's separate `/admin/`
retains its standard staff login; optionally create an account with
`python manage.py createsuperuser` using the virtual environment's Python.

Existing process environment variables take precedence over `.env`. If your shell
already supplies a different `DEBUG` value, use `$env:DEBUG = "True"` before running the local server
in PowerShell (or `DEBUG=True python manage.py runserver` on Linux/macOS) to enable
development static-file serving.

On Linux/macOS, create the environment with `python3 -m venv .venv`, activate it with
`source .venv/bin/activate`, and use `python` for subsequent commands.

## Behavior

- New Chat creates an empty saved chat and selects it.
- A question entered on the welcome page creates a chat automatically.
- The first question provides the chat title. History sorts by latest activity.
- Each submission atomically saves a user message and a pending ResearchQuery,
  then runs GPT, Claude, Gemini, Consensus, evidence preparation, and the final judge
  sequentially outside that transaction.
- Selecting a saved chat shows its messages in chronological order.
- AI Agent Research shows a separate collapsible card for each model with its answer,
  key findings, evidence, confidence, citations, or a safe error message.
- Existing Step 1 queries remain pending until the question is submitted again.
- Academic Evidence shows Consensus papers with authors, dates, journal, study type,
  abstracts, takeaways, citation counts, and safe source links when available.
- The final answer is primary: confidence, evidence strength, findings, sources,
  disagreements, and limitations appear above the individual research providers.
- Existing finished queries are preserved; submit a question again to run the full
  flow. Viewing old history does not trigger API requests. FinalResponse contains the
  accepted synthesis; a duplicate assistant Message is not created.
- Submissions use standard POST/redirect/GET with CSRF protection. JavaScript adds
  mobile navigation and an accessible processing indicator, and disables the submit
  button while researching. No AJAX is necessary for the synchronous flow.
- Bootstrap 5 CSS loads from jsDelivr; custom local CSS supplies the dashboard layout.

## Structure

```text
manage.py
research_ai/          settings, routes, WSGI entry point
chats/                chats/messages, forms, views, save service, admin, tests
research/             runner, evidence preparation/scoring/comparison, synthesis persistence
agents/               OpenRouter/Consensus/judge clients, schemas, prompts, safety helpers, tests
templates/            base, dashboard, final answer, AI research, academic evidence
static/css/           dashboard.css
static/js/            dashboard.js
.env.example          environment variable template
requirements.txt      Django 5.2 LTS, python-dotenv, requests, jsonschema
```

## OpenRouter architecture and configuration

`chats.services.save_question()` returns the newly created ResearchQuery, so the view
passes that exact query to `research.services.run_research()`. The runner claims a
pending query as processing with a conditional database update; an already processing
or finished query is not executed a second time. The runner calls `run_model()` for
each configured model. This independent unit is ready for a future parallel executor.

`agents.base.ResearchAgent` defines the client interface. `agents.openrouter` sends
non-streaming HTTPS requests to `https://openrouter.ai/api/v1/chat/completions` using
the same question and system prompt for every model. `agents.schemas` contains the
shared prompt, requested JSON Schema, local validation, and normalized contract.
Requests require providers that support the requested structured-output parameters.
The API transport is mocked in every test; tests never spend API credits.

Balanced model IDs were verified against OpenRouter's public `/api/v1/models` catalog
on September 22, 2026, including `response_format` and `structured_outputs` support:

| Card | Environment override | Default model ID |
| --- | --- | --- |
| GPT-6 Astra | `BALANCED_GPT_MODEL` | `openai/gpt-6-astra` |
| Gemini 3.1 Pro Preview | `BALANCED_GEMINI_MODEL` | `google/gemini-3.1-pro-preview` |
| Qwen3.8 Max | `BALANCED_THIRD_MODEL` | `qwen/qwen3.8-max-0902` |

The central model list lives in `research_ai/settings.py`. Replacement models must
support structured output. Availability and account access may change over time.
See [OpenRouter structured outputs](https://openrouter.ai/docs/guides/features/structured-outputs).

| Environment variable | Default | Purpose |
| --- | --- | --- |
| `OPENROUTER_API_KEY` | Empty | Required for live requests; keep it only on the server |
| `OPENROUTER_CONNECT_TIMEOUT` | `5` | Connection timeout per model, in seconds |
| `OPENROUTER_READ_TIMEOUT` | `120` | Read inactivity timeout per model, in seconds |
| `OPENROUTER_MAX_TOKENS` | `4096` | Output token budget per model |

The normalizer produces:

```json
{
  "provider": "openrouter",
  "model": "openai/gpt-6-astra",
  "label": "GPT",
  "answer": "Model answer",
  "key_findings": [],
  "evidence": [],
  "citations": [],
  "confidence": 75,
  "raw_response": {},
  "error": null
}
```

Confidence must be null or a number from 0 to 100. Citations contain `title`, `url`,
and `source_name`; unsafe or invalid URLs become empty strings. All model text is
escaped by Django templates. Citations remain within each normalized AgentResponse;
The three research agents do not populate Citation or FinalResponse records. Their sources are model claims,
not independently retrieved or verified evidence.

Each attempted model creates one AgentResponse. The full response envelope is stored
as JSON text in `raw_response`, and as an object within `normalized_response`.
Malformed non-JSON API bodies are retained under a `body` key. Credentials are redacted
before storage, including credentials echoed in model content. Request headers and
arbitrary exception messages are never logged or rendered by this integration.

Failures carry an `error` object with a stable `code` and safe `message`; answer lists
are empty and confidence is null. Authentication, credits, rate limits, network
failures, timeouts, refusals, truncation, and malformed output are handled separately.
A missing API key creates three configuration error records without making requests.

- At least one successful model or Consensus search: status `completed`, including
  partial results. A valid empty Consensus search is a successful search, explicitly
  displayed as finding no papers; it does not establish absence of evidence.
- All four attempts failed: status `failed`.
- Both terminal states set `completed_at`; per-model errors remain visible.
- Each model result is saved before the next call, without holding a network-spanning
  database transaction. No automatic retries or model substitutions occur.

## Consensus integration

The implementation follows the official
[Consensus search reference](https://docs.consensus.app/api-reference/query-for-relevant-papers),
reviewed September 20, 2026. It uses `GET https://api.consensus.app/v1/search`, with
the API key in the `x-api-key` header and the research question in the `query`
parameter. It does not use the deprecated `quick_search` endpoint.

`agents/consensus.py` owns the HTTP call; `agents/consensus_schemas.py` validates and
normalizes returned fields. `research/evidence.py` handles independent error capture,
AgentResponse storage, and citation persistence. `research/services.py` invokes it
after all three OpenRouter attempts, while the query is still processing. The only
OpenRouter client change is extracting its credential redactor into a shared helper
that protects both provider keys. The existing model request contract is unchanged.

The request selects page 0, excludes preprints, and requests semantic scores. Returned
order is preserved in the stored search response and Academic Evidence display.
The separate synthesis preparation layer prioritizes its bounded subset as described
below. Optional minimum year and study-type filters allow focused searches without
imposing a medical evidence hierarchy on every research topic. The supported filters
and response fields are documented in the search reference linked above.

| Environment variable | Default | Purpose |
| --- | --- | --- |
| `CONSENSUS_API_KEY` | Empty | Required server-side Consensus credential |
| `CONSENSUS_CONNECT_TIMEOUT` | `5` | Connection timeout in seconds |
| `CONSENSUS_READ_TIMEOUT` | `30` | Read inactivity timeout in seconds |
| `CONSENSUS_PAGE_SIZE` | `20` | Papers requested on the first page |
| `CONSENSUS_YEAR_MIN` | Empty | Optional earliest publication year |
| `CONSENSUS_STUDY_TYPES` | Empty | Optional comma-separated documented types, e.g. `systematic review,meta-analysis` |

The normalized result is:

```json
{
  "provider": "consensus",
  "query": "The submitted question",
  "summary": "",
  "papers": [],
  "key_findings": [],
  "error": null,
  "raw_response": {},
  "pagination": {}
}
```

Paper objects contain `title`, `authors`, `year`, `journal`, `abstract`, `url`, `doi`,
`citation_count`, `study_type`, `sample_size`, `relevance_score`, `published_date`,
`takeaway`, and `api_metadata`. Only returned metadata is populated. Unavailable
values remain empty strings, empty lists, or null. The API's `publish_year`,
`journal_name`, `semantic_score`, and `publish_date` map to the corresponding normalized
fields; `takeaway` values supply `key_findings`. This endpoint has no overall summary
field, so the Consensus result's `summary` stays empty. The separate judge creates
the final answer after this evidence is persisted.

One AgentResponse is stored with `provider="consensus"`, `model_name="search-v1"`,
and null confidence. `search-v1` identifies the endpoint, not a language model.
The redacted response body is saved as JSON text in `raw_response` and as an object
in the normalized result. A failure stores a static, safe error code/message instead
of exposing upstream exceptions; earlier model results remain intact.

Each unique academic paper becomes a Citation linked to its query and Consensus
response. Title, journal/source, URL, and an actual complete publication date go in
dedicated columns. DOI, authors, publication year, study type, sample size, citation
count, relevance, abstract, and additional returned metadata go in `metadata`.
Year-only dates never become invented January 1 publication dates. Original full
titles and journal names remain in metadata if database display columns need truncation.

Deduplication is scoped to a research query, using normalized DOI or URL (ignoring
fragments); absent identifiers fall back to exact normalized record equality. Similar
titles alone never merge papers. Existing citation records are reused and missing
metadata enriched. Re-saving the same Consensus result does not create another agent
record or another citation. No database-wide uniqueness constraint is added, since
the same paper can legitimately belong to different queries. Persistence is atomic
for the evidence result and its citations, separately from earlier OpenRouter saves.

Only validated HTTP(S) source URLs become links; credential-bearing URLs, local/private
IP literals, local hostnames, and unsafe schemes are rejected. Paper text is template
escaped. Links use `target="_blank" rel="noopener noreferrer"`; the application never
fetches those arbitrary source URLs. Raw bodies are not rendered on the dashboard.

Limitations: only the first page is requested; the dashboard indicates when more
results are available. Page size can be capped by the account plan. Metadata may be
missing, and a returned paper is not proof that its claims are correct. No optional
paid full-text excerpts are requested. See the official
[API plans and access](https://docs.consensus.app/api-plans-and-access) and
[Consensus API help](https://help.consensus.app/en/articles/16516328-the-consensus-api)
for account quotas and rate limits. A missing key creates a configuration error record
without contacting Consensus. Live account access must be checked with your own key.

## Final judge and evidence preparation

The Balanced judge is [`x-ai/grok-4.20`](https://openrouter.ai/x-ai/grok-4.20), configured
independently of the three research models. It uses the same server-side OpenRouter
key and chat-completions endpoint, with its own prompt, JSON schema, timeout, and
output budget. No raw provider envelopes or unnecessary API metadata are sent to it.

The flow is `research/preparation.py` → `agents/synthesizer.py` → validation in
`agents/synthesis_schemas.py` → persistence in `research/synthesis.py`. Preparation
selects successful agent findings and useful stored Consensus citations from the
current query only. Papers without an abstract or takeaway are excluded from the
judge context rather than treated as substantive evidence based on title alone.

| Setting | Default | Meaning |
| --- | --- | --- |
| `BALANCED_SYNTHESIZER_MODEL` | `x-ai/grok-4.20` | Balanced judge, independent of research models |
| `DEEP_DEFAULT_SYNTHESIZER` | `gpt` | Default Deep judge key; choices are gpt, claude, grok |
| `SYNTHESIZER_TIMEOUT` | `180` | Read inactivity timeout, seconds |
| `SYNTHESIZER_MAX_TOKENS` | `6000` | Output token budget |
| `SYNTHESIS_MAX_AGENT_CHARS` | `6000` | Text budget per agent across answer, findings, and citation text |
| `SYNTHESIS_MAX_AGENTS` | `3` | Maximum distinct successful research models included |
| `SYNTHESIS_MAX_PAPERS` | `8` | Highest-priority useful academic papers included |
| `SYNTHESIS_MAX_ABSTRACT_CHARS` | `2000` | Abstract excerpt limit per paper |
| `SYNTHESIS_MAX_CONTEXT_CHARS` | `80000` | Hard limit on the serialized evidence JSON |
| `SYNTHESIS_CLAIM_SIMILARITY` | `0.8` | Minimum lexical overlap for a tentative claim match |
| `SYNTHESIS_USE_RECENCY` | `False` | Explicit opt-in for topics where recent publication matters |

Per-field excerpt limits and omission notices are supplied to the judge and saved in
diagnostics. The source records remain unchanged. The answer gets up to 60% of each
agent text budget; up to eight findings and five unverified citation entries share
the remainder. Extra fields such as authors and takeaways have separate small caps.
Papers are prioritized before the paper-count limit. If the combined bounded context
still exceeds the hard limit, synthesis fails safely without a judge request. These
are character budgets, not exact token counts.

### Transparent priority scoring

Scores organize context; they do not choose the answer or establish scientific quality.
Weights are centralized in `SYNTHESIS_SCORE_WEIGHTS` and can be overridden by the
corresponding `SYNTHESIS_WEIGHT_*` variables listed in `.env.example`.

| Signal | Default weight | Rule |
| --- | --- | --- |
| Relevance | 40 | Rank of a returned semantic score among this query's distinct scores; no assumed absolute score scale |
| Review design | 15 | Returned study type is systematic review or meta-analysis |
| Peer review | 5 | Only when returned `is_preprint` is explicitly false |
| Journal quartile | 5 | Scaled by returned SJR quartile; journal name alone earns nothing |
| Citations | 3 | Log-scaled, capped at 1,000 citations |
| Sample size | 2 | Log-scaled, capped at 10,000; a weak context signal only |
| Recency | 3 | When enabled: linear decay over ten years, using the reported year |

Unknown metadata earns no bonus. Agent priority uses only supplied citation count
(weight 0.5, capped at five) and links matching selected academic sources (weight 2,
capped at three). A matching link does not prove that the source supports the claim.
AI confidence and agreement count never add score. Scores across agents and papers
are not compared as interchangeable quality measurements. Ties are deterministic.

`research/claims.py` compares major findings using conservative token overlap and
simple negation. It reports possible agreement, possible contradictions, independent
agent support counts, candidate academic matches, and findings with no text match.
These are leads for the judge to review, not verified support. It does not resolve
semantic disagreement, subtle negation, or prove that a claim lacks external support.

### Source traceability and output contract

Internal IDs are stable database mappings: `C<pk>` identifies a Citation and `A<pk>`
identifies a stored AgentResponse. Only IDs for evidence actually included in the
context are allowed. The result retains source snapshots and database IDs for review.
Agent sources are explicitly labeled unverified model output and link to the stored
response; they are not converted into fabricated academic citations.

The judge must return:

```json
{
  "final_answer": "A direct answer with source IDs such as [C12]",
  "executive_summary": "A concise summary",
  "key_findings": [{
    "claim": "A finding",
    "evidence_strength": "limited",
    "explanation": "Why the evidence supports this interpretation",
    "supporting_source_ids": ["C12"]
  }],
  "agreements": [],
  "disagreements": [],
  "limitations": [],
  "recommended_interpretation": "A cautious interpretation",
  "overall_confidence": null,
  "source_ids": ["C12"]
}
```

All fields are required. Finding strength is `strong`, `moderate`, `limited`, or
`conflicting`. Confidence is null or 0–100: the judge's estimate, not a statistical
probability or mechanically computed evidence score. Unknown IDs in structured
lists are removed with a limitation notice, and confidence is withheld. Unknown
references in prose, new URL/DOI bibliography text, extra citation objects, and
malformed output are rejected. Findings labeled strong/moderate without an attached
academic source are reduced to limited; this validation does not establish that an
attached source actually entails the claim. The judge never creates Citation rows.
Sources and clickable links are reconstructed exclusively from supplied records.

### Persistence, status, and failure behavior

`FinalResponse.answer` stores `final_answer`, `confidence` stores the judge estimate,
and `synthesis_data` stores the complete normalized result plus model, source mappings,
validation notes, preparation diagnostics, and generation time. Re-running the
synthesis service updates the same OneToOne record. A failed later attempt preserves
a previously accepted answer and records `last_attempt_error`.

The existing ResearchQuery lifecycle remains `pending → processing → completed/failed`.
Collection, evidence preparation, and synthesis all happen while processing, and
`completed_at` is set afterward. Query status describes collection usability: at least
one successful model or search keeps it completed even if synthesis is unavailable.
Synthesis has its own `completed`, `failed`, or `insufficient_evidence` state inside
`FinalResponse.synthesis_data`. A successful empty search can therefore complete
collection while synthesis correctly reports insufficient evidence. This preserves
existing status semantics without a schema migration.

If no successful agent finding or useful academic paper remains, the judge is not
called. The UI shows “Insufficient evidence to produce a reliable final synthesis.”
Other judge failures show “Final synthesis unavailable” and retain all provider data.
If academic evidence is unavailable but agents remain, synthesis can proceed with
an explicit provisional-evidence limitation.

The `research.synthesis` logger records lifecycle events, query IDs, evidence counts,
and safe error codes. It does not log questions, complete prompts, source text,
upstream exception bodies, or API keys.

### Security and limitations

The judge receives a fixed system message and one JSON user message. All research
content, including the question, abstracts, titles, and agent responses, is untrusted
data. The prompt explicitly rejects embedded instructions and majority-vote reasoning.
Credentials are redacted; output text is template-escaped; final URLs are validated
again before rendering. Prompt boundaries reduce injection risk but do not prove that
a language model will always ignore adversarial content.

Source-ID validation establishes provenance, not factual correctness or entailment.
The simple English claim matcher can miss paraphrases and produce false matches;
review disagreements against the actual source text. Priority weights are heuristics,
not a validated evidence-grading framework; sample sizes and citation counts are not
comparable across all study designs. Recency is off by default. Context caps can omit
important evidence, and available excerpts may not include full study limitations.
The configurable judge may be from the same model family as a research agent, so it
is not an independent scientific verifier. Sequential requests still occupy a web
request, and live quality/account access requires validation with real provider keys.

## Validation

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\.venv\Scripts\python.exe manage.py test
```

Tests cover chat creation, persistence, selection, ordering, escaping, validation,
CSRF protection, transaction rollback, admin registration, and model relationships,
plus OpenRouter success, malformed output, HTTP/API errors, timeouts, credential
redaction, partial failures, result persistence, status transitions, and safe rendering.
Consensus tests additionally cover documented request fields, missing metadata, empty
searches, citation deduplication, metadata persistence, isolated persistence failure,
and successful academic search when every conversational model fails. All provider
HTTP calls are mocked or disabled by an explicitly empty test credential.
Step 4 adds source validation, scoring/preparation, claim comparison, context limits,
injection-boundary, judge-client, persistence, failure, and complete dashboard-flow
tests. Earlier provider regression tests isolate the synthesis service; the new
full-flow tests run the entire implementation with mocked HTTP transport.

## PythonAnywhere compatibility

Follow [DEPLOY_PYTHONANYWHERE.md](DEPLOY_PYTHONANYWHERE.md) for the deployment
checklist, private environment configuration, Web tab WSGI setup, static mapping,
HTTPS, logging, and update commands. `.env.example` defaults to `DEBUG=False`;
use `DEBUG=True` and localhost `ALLOWED_HOSTS` explicitly for local development.

The app uses WSGI, SQLite, and ordinary Django requests with no persistent worker
process. Upload the project, create a virtual environment using a compatible Python
version, and install `requirements.txt`. Set `SECRET_KEY`, `DEBUG=False`, and
`ALLOWED_HOSTS=your-username.pythonanywhere.com` in the project `.env` or environment.
If needed, set `CSRF_TRUSTED_ORIGINS=https://your-username.pythonanywhere.com`.
Also set both provider API keys and ensure the hosting account permits outbound HTTPS
to `openrouter.ai` and `api.consensus.app`.

Configure the web app's virtual environment and WSGI file to include the project:

```python
import os
import sys

sys.path.insert(0, "/home/your-username/research_ai")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "research_ai.settings")
from django.core.wsgi import get_wsgi_application
application = get_wsgi_application()
```

Run `python manage.py migrate` and `python manage.py collectstatic --noinput` in the
virtual environment. Map `/static/` to `/home/your-username/research_ai/staticfiles`
in the hosting configuration and serve over HTTPS. The `.env` is loaded relative to
the project, independently of the WSGI working directory.

Three research model calls, Consensus, and one judge call hold one web request open. Set timeouts and token
budgets to fit the hosting account's request limits; the read timeout is an inactivity
limit, not a guaranteed total wall-clock limit. Long responses can still exceed a
hosting timeout. If the host terminates the process, already stored responses remain,
but the query can remain `processing`. Step 6 adds explicit stale-run recovery using
`recover_research`; there is still no automatic recovery or retry worker.

The dashboard intentionally has no authentication and shares all stored chats with
any visitor who can reach it. Keep a hosted instance private with hosting access
controls if the research is private. The Django admin remains password protected.

## Step 5: attempts, diagnostics and manual evaluation

Each actual provider call now creates an `AgentResponse` attempt. `SynthesisAttempt`
stores every accepted or failed judge attempt; `FinalResponse` points to the active
accepted answer. Failures never erase earlier successful evidence or answers.
Consensus attempts keep their paper snapshots and memberships while sharing deduplicated
Citation identities. Evidence preparation uses the latest successful attempt per provider
slot, including its original paper text. Existing database records are preserved by
migrations 0002 and 0003; historical timing, token usage and cost remain unknown.

The dashboard offers Retry GPT/Claude/Gemini/Consensus, Retry Failed Providers and
Re-run Final Synthesis. Retry Failed Providers retries only missing or failed latest
research providers; synthesis has its own button. Provider retries do not silently
run synthesis. Changed active evidence marks the accepted answer as needing re-synthesis.
The judge selector only accepts IDs in `SYNTHESIZER_MODELS`, plus `SYNTHESIZER_MODEL`.
Re-synthesis sends saved evidence to one judge without calling the research providers.
History labels Latest and Active separately; earlier answers remain inspectable.
POST and CSRF protections apply to every paid action. Submission/action tokens and a
conditional database lease prevent duplicate active executions. A terminated process
can leave processing set; recovery requires an explicit command with `--live`.

Configuration (add to the existing `.env`; do not replace your secret):

```dotenv
RESEARCH_DIAGNOSTICS_ENABLED=True
RESEARCH_COST_TRACKING_ENABLED=True
SYNTHESIZER_MODELS=
OPENROUTER_MODEL_PRICING={}
```

`SYNTHESIZER_MODELS` is a comma-separated list of additional approved judge IDs.
For example, add the currently configured GPT model ID to compare two judges.
The diagnostics switch hides developer metrics, readiness and evidence-preparation
details, and disables evaluation routes. Retry controls and attempt history remain.

Timing uses a monotonic clock, with UTC start/completion timestamps. Research duration
is the cumulative wall time of collection/synthesis operations and subsequent retries,
including local preparation/persistence; it excludes idle time between user actions.
Provider counts describe latest attempts; paper/answer diagnostics describe active
results, which may precede a newer failure. Token counts are recorded only when supplied.

Cost is per OpenRouter attempt, including the judge and failed responses that supply
usage. The application prefers `usage.cost`, as documented by
[OpenRouter usage accounting](https://openrouter.ai/docs/cookbook/administration/usage-accounting).
Otherwise, `OPENROUTER_MODEL_PRICING` may supply verified USD rates per million tokens:
each exact model ID maps to `input_per_million` and `output_per_million`. There are no
invented default rates. Both token counts and rates are required for a configured estimate;
cache/multimodal token details prevent this simple fallback. Operators must ensure rates
apply to the selected model/provider/context tier. Reported and configured costs are
labeled separately. Unknown cost is null. If any attempt cost is missing, the overall
total is unknown and only a clearly labeled known-cost subtotal is shown. These are
OpenRouter costs, not a complete API bill: Consensus billing is not estimated.

`/evaluations/` loads six cases from `research/evaluation_cases.json`. Cases contain a
question, category, expected topics, important source guidance and notes. Viewing cases
is offline; **Run case (live APIs)** explicitly starts the full pipeline and saves a
case snapshot with the query. Results show counts, latency, costs, agreement/disagreement
counts, evidence strength distribution, confidence and answer length. Valid source
percentage checks final retained IDs against real records belonging to that query.
It is null with no cited IDs; removed IDs are reported separately. Neither 100% valid
IDs nor a high confidence value establishes factual correctness. Findings include
“Supporting sources selected by synthesizer” with mapped details for human review.

## First real API test

1. Open PowerShell in the project directory. Keep the existing `.env` and `SECRET_KEY`.
   Add valid `OPENROUTER_API_KEY` and `CONSENSUS_API_KEY` values privately, along with
   the two diagnostics flags above. Keep `DEBUG=True` for this local test. Leave
   pricing `{}` unless you have verified rates. Restart Django after changing `.env`.
2. Install existing dependencies and validate locally:

   ```powershell
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   .\.venv\Scripts\python.exe manage.py migrate
   .\.venv\Scripts\python.exe manage.py check
   .\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
   .\.venv\Scripts\python.exe manage.py test
   .\.venv\Scripts\python.exe manage.py research_smoketest
   ```

   The smoke command prints Configured/Missing, never key values. Without `--live`
   it checks local configuration, database migrations and the evaluation fixture,
   makes no network requests and cannot verify credential validity or account access.
   Missing credentials are reported without failing this offline check.
3. Intentionally authorize small live checks with these commands:

   ```powershell
   .\.venv\Scripts\python.exe manage.py research_smoketest --live --provider openrouter
   .\.venv\Scripts\python.exe manage.py research_smoketest --live --provider consensus
   ```

   Each command makes at most one request. OpenRouter uses the configured GPT model
   and a 512-output-token cap; Consensus requests one paper. Reasoning models may
   exhaust this small output budget, producing a safe validation failure. There is
   no automatic retry. `--live` alone checks both providers. The command never saves
   research, invokes a judge, or prints response bodies. Live failures return nonzero.
4. Start the local server:

   ```powershell
   .\.venv\Scripts\python.exe manage.py runserver
   ```

   Open http://127.0.0.1:8000/, verify both readiness values, and submit
   “What does research suggest about sleep and memory consolidation in healthy adults?”
   once. This full run makes three research-model requests, one Consensus request and,
   if usable evidence exists, one judge request. Keep the page open until it finishes.
5. Inspect the answer, all provider cards, paper relevance, source mappings, attempt
   history and Developer diagnostics. Note time, known/unknown cost and any failures.
   Retry a single provider and confirm only its history grows. Re-run Final Synthesis
   and confirm provider attempt counts do not change. Check that an older answer remains
   in synthesis history. Use Retry Failed Providers only when you intend those calls.
6. Open `/evaluations/` and run remaining cases individually. Cover medical/scientific,
   technology, economics, general factual, conflicting evidence and little-evidence
   questions. Compare alternate configured judges on the same saved evidence if useful.

For **every** real test, record a manual review alongside its query/attempt IDs:

- Did all research models receive the same question?
- Are the Consensus papers relevant to the question and population?
- Are citations real, correctly mapped and clickable?
- Does the final answer reflect the supplied evidence, including limitations?
- Are disagreements disclosed and is confidence reasonable?
- Did the judge invent claims, facts or sources?
- Was strong or conflicting evidence omitted by retrieval or context limits?
- What was the total response time and approximate cost? Which costs are unknown?

No automated test spends credits; all external calls in tests are mocked. Step 5 adds
no packages, workers, authentication, multi-user behavior or deployment changes.

## Step 6: reliability and evidence review

Provider policy is centralized in `agents/budgets.py` and Django settings. All providers
are enabled by default. Set `GPT_ENABLED`, `CLAUDE_ENABLED`, `GEMINI_ENABLED`,
`CONSENSUS_ENABLED` or `SYNTHESIS_ENABLED` to False to stop future calls to that step.
Saved evidence remains available even after a provider is disabled. Disabled providers
are excluded from missing-step recovery and retry controls; disabling a provider does
not delete its history. Low-level smoke checks also respect enabled flags.

`OPENROUTER_MAX_RETRIES` and `CONSENSUS_MAX_RETRIES` default to **0** so upgrading does
not silently increase spending. Set them to 1 or 2 when ready to enable retries. They
mean additional attempts after the first, with a maximum configurable value of 5.
`PROVIDER_RETRY_BACKOFF=2` waits 2, 4, 8… seconds, capped at 30 seconds per delay.
Each attempt is committed separately before the next retry; no transaction stays open
during network calls or backoff. Retry costs and latency remain visible in history.

Each of GPT, CLAUDE, GEMINI and SYNTHESIS supports `_TIMEOUT`, `_MAX_TOKENS`,
`_MAX_RETRIES` and `_RETRY_BACKOFF` overrides. Empty overrides inherit shared settings:
research agents use OPENROUTER_READ_TIMEOUT/OPENROUTER_MAX_TOKENS; synthesis uses
SYNTHESIZER_TIMEOUT/SYNTHESIZER_MAX_TOKENS. Consensus uses CONSENSUS_READ_TIMEOUT or
CONSENSUS_TIMEOUT, CONSENSUS_MAX_RETRIES and optional CONSENSUS_RETRY_BACKOFF. Its
output budget is CONSENSUS_PAGE_SIZE; it has no generated-token parameter. Existing
connection-timeout settings still apply. Per-step enabled flags and budgets appear
in `.env.example`; all numeric budgets must be positive except retry count/backoff,
which may be zero.

Errors retain existing safe messages/codes and add a category: timeout, authentication,
rate_limit, provider_unavailable, malformed_response, invalid_request, network_error
or unknown. Only timeout, rate_limit, network_error and HTTP 5xx availability failures
are automatically retried. Authentication, billing, invalid requests, malformed output,
refusals and unknown errors are not. Changing credentials/configuration and explicitly
retrying later is possible. JSON log records contain query ID, provider, model, attempt
number, status, latency and error category—never prompts, raw provider errors or keys.

### Interrupted-run recovery

```powershell
.\.venv\Scripts\python.exe manage.py recover_research
.\.venv\Scripts\python.exe manage.py recover_research --query-id 123
# Explicitly allow potentially billable recovery for the inspected query:
.\.venv\Scripts\python.exe manage.py recover_research --live --query-id 123
# Collect only missing evidence; re-synthesize manually afterward:
.\.venv\Scripts\python.exe manage.py recover_research --live --query-id 123 --skip-synthesis
```

Without `--live`, recovery only reads and reports stale processing queries. A renewable
database lease expires after RESEARCH_STALE_AFTER_SECONDS (default 900 seconds), or
the largest configured read+connect timeout plus 60 seconds, whichever is larger.
The lease is renewed before calls, before persisting results and before retry delays.
Legacy processing queries without leases use their start/creation time. Recovery
atomically claims an expired lease, rechecks saved successes, and resumes only missing
or failed steps from the interrupted operation. An interrupted synthesis-only action
does not start new research providers. A successful judge result for the same active
evidence and model is reused. Successful provider attempts are never duplicated by
recovery, even if an older successful result exists before a later failure.

Execution stages are COLLECTING_AGENTS, COLLECTING_CONSENSUS, PREPARING_EVIDENCE,
SYNTHESIZING, COMPLETED, PARTIAL and FAILED (plus PENDING before execution). The existing
status remains compatible: completed means some research is available; stage PARTIAL
identifies missing providers, outdated synthesis or a failed judge. UI shows the current
step, failures, partial results, available retries and recovery provenance.

An execution token is checked inside the same short transaction as every result save;
an old runner that loses ownership cannot overwrite the new execution's results or
continue to its next provider. Browser forms supply UUID submission and action keys.
Replaying the same keyed POST returns its existing query/action; reusing a key for a
different payload is rejected. Legacy POST clients that omit keys retain compatibility
but cannot receive replay protection across completed submissions. New Chat is local
only and does not spend credits. Rating POSTs also never call APIs.

Closing the browser does not necessarily stop Django; inspect the saved query first.
Only recover after an execution is stale. Requests timeouts are inactivity limits,
[not absolute wall-clock deadlines](https://requests.readthedocs.io/en/latest/user/quickstart/#timeouts).
A slow response can outlive a lease, and a crash after provider acceptance but before
local persistence leaves the remote outcome unknown. The app fences database writes,
but cannot guarantee exactly-once remote billing or cancel an already sent HTTP call.
Recovery may need to repeat that unknown call. Recovered diagnostics flag that duration
and costs for unsaved interrupted work may be incomplete. Review provider billing when
testing interruptions. There is no scheduled recovery, Celery, Redis or deployment.

### Evidence selection and diagnostic support

Selection removes duplicate DOI/URL/exact-record identities, ranks available semantic
relevance plus question-word overlap, and gives a small completeness preference to
actually supplied metadata. A greedy title-similarity penalty promotes diversity among
similarly ranked papers; distinct identities are retained and omitted papers are listed.
Similarity is English word overlap, not an assessment of scientific agreement. Important
opposing evidence can still be missed. No missing study quality is invented: a non-preprint
flag alone is not treated as proof of peer review. Inspect preparation diagnostics.

Thresholds are advisory, so an otherwise usable single-source result can still be
synthesized:

```dotenv
MIN_CONSENSUS_PAPERS_FOR_STRONG_EVIDENCE=2
MIN_VALID_SOURCES_FOR_SYNTHESIS=1
MIN_SUCCESSFUL_AGENTS=1
```

Each final finding stores diagnostic support metadata:

- `academically_supported`: a mapped academic excerpt shares at least two nontrivial
  English words with the claim, and the judge did not label the evidence limited.
- `agent_supported_only`: valid references point only to stored AI responses.
- `conflicting`: the judge labels evidence conflicting and supplies traceable references.
- `weak_support`: academic references exist but lexical overlap is weak or the judge
  marks the evidence limited.
- `no_traceable_support`: there are no valid mapped references.

These names are review flags, not entailment or factual correctness labels. Strong
claims without enough valid academic references or lexical support receive a limited
**diagnostic** evidence strength and a visible mismatch flag. The original declared
strength is retained. Answer prose is not rewritten; existing citation-validation
safeguards still apply. Paper counts alone never establish strong evidence.

### Live evaluation and comparison

After a run finishes, expand Developer evaluation and rate answer quality, citation
quality, evidence relevance and synthesis quality from 1 to 5, plus notes. Ratings are
append-only and refer to the active synthesis attempt reviewed; they never feed the
judge. Refresh if the answer changed before saving a rating. `/evaluations/` compares
the latest 100 runs, filterable by question, including model IDs, latest judge, total
known/unknown cost, cumulative latency, paper count and manual ratings. A rating may
refer to an older answer; its attempt ID is shown. Disable RESEARCH_DIAGNOSTICS_ENABLED
to hide and disable developer evaluation routes.

For the first full live Step 6 test:

1. Keep your existing SECRET_KEY and set both API credentials privately in `.env`.
   Enable all five providers and diagnostics. Start with OPENROUTER_MAX_RETRIES=0 and
   CONSENSUS_MAX_RETRIES=0 to measure the baseline; use 1 later to test transient retry.
2. Run `python manage.py migrate`, `python manage.py check`,
   `python manage.py makemigrations --check --dry-run`, `python manage.py test`,
   `python manage.py research_smoketest` and `python manage.py recover_research`.
   These commands make no real API calls; tests mock providers.
3. Intentionally run `python manage.py research_smoketest --live --provider openrouter`
   and `python manage.py research_smoketest --live --provider consensus`. Each makes
   at most one small request; smoke commands do not automatically retry.
4. Start `python manage.py runserver`, open http://127.0.0.1:8000/, and submit the sleep
   and memory question from the earlier checklist once. Expect three agent calls, one
   academic search and one judge call when evidence is usable; enabled automatic
   retries can add calls. Record the query ID and wait for completion.
5. Check final stage, all attempt histories, paper relevance/diversity, rejected source
   IDs, support flags, strong-evidence mismatches, time and cost. Save the four manual
   ratings and notes. Compare another evaluation case or judge using `/evaluations/`.
6. If a run is naturally interrupted, first run the recovery dry run for its query ID.
   Once stale, explicitly use `--live --query-id ID`; verify that saved successes were
   reused. Do not lower the stale interval simply to take over a still-running request.

Use the existing virtual-environment Python path shown above for all Windows commands.
No new packages are required.

## Temporary OpenRouter free test mode

Add the following to your existing `.env` without deleting or editing the paid model
settings or credentials:

```dotenv
OPENROUTER_FREE_TEST_MODE=True
FREE_TEST_MODEL_1=nex-agi/nex-n2.5-pro:free
FREE_TEST_MODEL_2=dots-studio/dots-3-note-preview:free
FREE_TEST_MODEL_3=nvidia/nemotron-3-super-120b-a12b:free
FREE_TEST_SYNTHESIZER_MODEL=nex-agi/nex-n2.5-pro:free
```

True enables temporary free models; False restores the existing
the Balanced and Deep research/judge settings. Premium Deep judge controls are hidden
in free mode, and server validation prevents paid judge requests regardless of browser input.
Those settings and their values remain intact. The flag defaults to False when omitted;
`.env.example` now defaults to paid production mode. Switch just the flag to True for
free testing. Restart Django whenever changing modes.

Selection is centralized in `research_ai/model_config.py`, loaded by settings. The
three explicit IDs were checked on **2026-09-21** against the public
[OpenRouter model catalog](https://openrouter.ai/api/v1/models) and each selected model's
`/endpoints` listing: prompt and completion prices were zero, with structured_outputs
and response_format advertised. This is catalog verification, not an inference or
quality test. Availability, capacity and account rate limits can still prevent a run.
An OpenRouter API key is still required. Consensus credentials/access/billing remain
separate and unchanged; this flag only makes the OpenRouter portion free.

Requests retain strict JSON schemas and source validation. Free mode applies a central
zero-price provider ceiling for prompt, completion and request charges and excludes
paid alternate judges. Unknown free overrides fail configuration validation rather
than relying on the name `:free`; verify new IDs against the catalog before adding them
to the central allowlist. If a free endpoint disappears or no longer qualifies, the
request fails safely instead of switching to a paid model.

OpenRouter's [free router](https://openrouter.ai/openrouter/free) was considered: it
selects compatible free models dynamically, which makes comparisons less reproducible.
The application therefore defaults to three explicit IDs. `openrouter/free` is an
optional verified override, but neither `openrouter/auto` nor an assumed `auto:free`
route is used. Provider zero-price routing is described in
[OpenRouter provider selection](https://openrouter.ai/docs/guides/routing/provider-selection#max-price).

The dashboard shows FREE TEST MODE, Agent 1/2/3, and actual configured model IDs.
Internal gpt/claude/gemini slot keys and budgets stay unchanged to preserve retry/history
and recovery behavior; they do not identify the temporary models' vendors. Historical
paid results keep their original labels and IDs. For a clean comparison, create a new
chat after changing modes instead of mixing old evidence and new retries.

To test, stop the existing development server with Ctrl+C, then run:

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py research_smoketest
.\.venv\Scripts\python.exe manage.py runserver
```

Open http://127.0.0.1:8000/, confirm FREE TEST MODE and the IDs, create a new chat and
submit one question. No schema migration or dependency change is required. All automated
tests use mocks; the existing regression suite expects production defaults, so run it
with `OPENROUTER_FREE_TEST_MODE=False` in the process environment if your `.env` enables
free testing. The new tests explicitly exercise both modes, including the whole mocked
free pipeline. Clear that process override before restarting in free mode.

## Balanced topic discovery and Deep judge selection (verified 2026-09-22)

Model IDs, prices, context windows and JSON-schema-capable endpoints were checked
against the public [OpenRouter catalog](https://openrouter.ai/api/v1/models) without inference.
All models below advertise response_format and structured_outputs. Capability listings
are not a live benchmark, an availability guarantee or confirmation of account access.

| Balanced role | Model ID | Input / output USD per 1M text tokens | Context |
| --- | --- | --- | --- |
| GPT research | `openai/gpt-6-astra` | $10 / $50 | 1,050,000 |
| Gemini research | `google/gemini-3.1-pro-preview` | $2 / $12 | 1,048,576 |
| Qwen research | `qwen/qwen3.8-max-0902` | $2 / $6 | 1,000,000 |
| Grok synthesis | `x-ai/grok-4.20` | $1.25 / $2.50 | 2,000,000 |

The catalog also lists long-input tiers: Astra at 272,000+ input tokens is $20/$75,
Gemini at 200,000+ is $4/$18, and Grok at 200,000+ is $2.50/$5. Routing, caching and
non-text inputs can change charges. Consensus remains separately billed and configured.
Current budgets remain 4096 output tokens / 120-second read timeout per research agent,
and 6000 tokens / 180 seconds for synthesis. Retries remain opt-in; defaults are zero.

```dotenv
OPENROUTER_FREE_TEST_MODE=False
RESEARCH_MODE=balanced
BALANCED_GPT_MODEL=openai/gpt-6-astra
BALANCED_GEMINI_MODEL=google/gemini-3.1-pro-preview
BALANCED_THIRD_MODEL=qwen/qwen3.8-max-0902
BALANCED_SYNTHESIZER_MODEL=x-ai/grok-4.20
DEEP_SYNTHESIZER_GPT_MODEL=openai/gpt-6-astra
DEEP_SYNTHESIZER_CLAUDE_MODEL=anthropic/claude-fable-5.1
DEEP_SYNTHESIZER_GROK_MODEL=x-ai/grok-4.20
DEEP_DEFAULT_SYNTHESIZER=gpt
```

RESEARCH_MODE controls the initial composer choice. The composer offers Balanced and
Deep Research; there was no Economy configuration in this repository, and none has
been invented. Selecting Deep shows a Synthesizer dropdown with GPT-6 Astra (default),
Claude Fable 5.1 and Grok 4.20. Cost hints are descriptive, not price quotations.
Fable's catalog rate is $10/$50 per million text tokens with a 1M-token context.

Deep research agents retain the prior GPT + Gemini + Claude Opus 5 configuration.
GPT_MODEL, GEMINI_MODEL and CLAUDE_MODEL remain their legacy defaults; optional
DEEP_GPT_MODEL, DEEP_GEMINI_MODEL and DEEP_CLAUDE_MODEL take precedence. Balanced uses
only the BALANCED_* variables. SYNTHESIZER_MODEL is preserved for legacy configuration;
new run selection uses the Balanced or Deep judge settings above. No model IDs live
in services or templates. Qwen reuses the historical internal `claude` retry/budget
slot (CLAUDE_TIMEOUT/MAX_TOKENS/ENABLED), but its current UI label is Qwen3.8 Max.

Mode, initial judge key, exact judge ID and agent configuration are persisted in the
existing ResearchQuery.execution_data JSON. No migrations are required. Retries and
recovery retain this selection. History continues to identify the actual models used,
including historical Claude responses; changing a mode does not relabel saved evidence.

For a saved Deep run, open View Detailed Research -> Retry controls, choose another
Synthesizer, and click Re-run Final Synthesis. Only the judge calls OpenRouter using
saved evidence. Earlier SynthesisAttempt rows remain; the latest accepted result stays
active if a later judge fails. History shows friendly names, exact IDs, confidence,
latency, estimated cost, timestamps and success/failure, without ranking judges.
Only gpt/claude/grok choice keys are accepted from the Deep dropdown; arbitrary browser
model IDs are rejected. Re-synthesis can incur charges and never recollects Consensus.

OPENROUTER_FREE_TEST_MODE=True takes priority over both modes. It retains the existing
three free research models and free judge; premium judge selection is hidden and blocked.
The saved free selection is retained when revisiting or retrying an existing free run.

### Topic-discovery prompts

Explicit English requests for thesis/research topics, topic ideas, or suggestions of
research questions/gaps use the topic-discovery schema. Normal questions retain the
existing generic schema. Intent detection is a small deterministic phrase matcher,
not another model call; ambiguous requests should explicitly say 'suggest thesis topics'.

Agents propose at most three feasible Master's-level ideas with problem_area,
topic_ideas (title, research_gap, why_it_matters, population, main_variables,
possible_research_question, methodology_idea, feasibility, evidence_needed), key_findings,
uncertainties, citations and confidence. The full structured ideas remain in the
AgentResponse JSON; a bounded readable answer/evidence adapter supports the existing
preparation pipeline and dashboard without database or layout changes.

The judge compares ideas against supplied academic literature and metadata, favors
relevant recent evidence, distinguishes 'under-researched' from 'not found in this
search', and recommends at most three topics using the existing synthesis schema.
It must explain population, variables, question, method, feasibility and limitations.
Few papers do not establish a gap; source IDs remain strictly validated. Consensus
retrieval, scoring, source validation, retries and recovery scheduling are unchanged.

After modifying frontend assets, run collectstatic and hard-refresh the browser.
For local development (the DEBUG override below does not affect paid/free selection):

```powershell
$env:DEBUG = "True"
.\.venv\Scripts\python.exe manage.py collectstatic --noinput
.\.venv\Scripts\python.exe manage.py runserver
```

## Step 7 recommendation

Use measured live results to choose the next change: enforce a per-run spending/time
budget and improve visibility into in-flight requests, then tune retrieval using manual
ratings. If synchronous latency is unacceptable, separately scope a durable job queue
with polling and cancellation before deployment; do not add workers based on guesswork.
