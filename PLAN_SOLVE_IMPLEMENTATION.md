# Plan-and-Solve implementation report

Validated on 2026-09-25. Production Balanced now uses independent query planners,
hybrid academic retrieval and evidence-grounded synthesis. Deep, existing free-test
workflow, saved history, chat management, retry controls and recovery are preserved.
The dashboard layout is unchanged; labels, evidence metadata and diagnostics were added.

## Models and endpoints

| Role | OpenRouter model |
| --- | --- |
| Planner 1 | `google/gemini-3.5-flash` |
| Planner 2 | `meta-llama/llama-4-scout` |
| Planner 3 | `qwen/qwen3.5-27b` |
| Primary synthesizer | `deepseek/deepseek-v4-flash` |
| Optional synthesizer | `openai/o4-mini` |

All five appeared in the public OpenRouter catalog with structured-output support.
No paid inference was performed. Independent planners run sequentially on the
existing request/lease infrastructure; no additional orchestration service is required.

- Semantic Scholar: `GET https://api.semanticscholar.org/graph/v1/paper/search`.
- Consensus: existing `GET https://api.consensus.app/v1/search`.
- OpenRouter: existing `POST https://openrouter.ai/api/v1/chat/completions`.
- Recommendations expansion is optional future work and is not called.

## Stored schemas and evidence handling

ResearchPlan: `original_question`, `canonical_search_queries`, `keywords`, `synonyms`,
`research_subquestions`, `candidate_gap_hypotheses`, `uncertainties`, `filters`,
`generated_query_count`, `successful_planners`. It is stored in the existing
`ResearchQuery.execution_data` JSON field. Each provider attempt retains the exact
plan used for its retrieval requests. Planner JSON includes the requested population,
exposure and outcome terms and remains stored in its AgentResponse.

AcademicEvidence: `source_provider`, `source_id`, `title`, `authors`, `year`,
`journal_or_venue`, `abstract_or_snippet`, `url`, `doi`, `citation_count`,
`influential_citation_count`, `study_type`, `sample_size`, `relevance_score`,
`open_access`, `metadata`. Merging adds `identity_keys`, `source_providers` and
`provider_records`; ranking adds an inspectable `priority` object. Missing metadata
remains missing. Citation records accumulate provenance while attempt and synthesis
snapshots preserve historical evidence.

Deduplication matches DOI first, then Semantic Scholar/external IDs, canonical URL,
then normalized title plus known year. Conflicting DOIs prevent fallback merging.
Ranking prioritizes query/subquestion overlap and supplied normalized Consensus
relevance. Capped citation influence, study type and optional recency are secondary;
venue diversity is applied within relevance tiers. Foundational/recent/highly_relevant/
supporting labels organize papers, never certify truth or study quality.

Synthesis receives bounded snippets from deduplicated academic sources, the plan and
short planner insights. It receives no raw provider payloads. Internal IDs `S<pk>`,
`C<pk>` and `M<pk>` map back to existing Citation records. Unknown IDs reject a new
Balanced answer, retaining any previously accepted answer. Planner-only output is
insufficient for synthesis. Failures of one retrieval provider or planner do not
discard the others. Retry/recovery and alternative synthesis are covered by tests.

## Files

Created:

- `agents/planner.py`
- `agents/semantic_scholar.py`
- `research/academic.py`
- `research/hybrid.py`
- `research/planning.py`
- `research/test_plan_solve.py`
- `research_ai/test_runner.py`
- `PLAN_SOLVE_IMPLEMENTATION.md`

Modified:

- `agents/budgets.py`, `agents/openrouter.py`, `agents/security.py`,
  `agents/synthesis_schemas.py`, `agents/synthesizer.py`, `agents/test_topic_discovery.py`
- `research/configuration.py`, `research/execution.py`, `research/metrics.py`,
  `research/preparation.py`, `research/recovery.py`, `research/services.py`,
  `research/synthesis.py`, `research/test_deep_selector.py`, `research/test_free_mode.py`,
  `research/test_production_models.py`, `research/test_synthesis.py`
- `research_ai/model_config.py`, `research_ai/settings.py`, `research_ai/logging.py`
- `chats/presentation.py`, `chats/views.py`, `chats/tests.py`
- `templates/dashboard.html`, `templates/research/academic_evidence.html`,
  `templates/research/agent_research.html`, `templates/research/attempts.html`,
  `templates/research/paper_preview.html`, `templates/research/snapshot.html`
- `.env.example`, `README.md`, `DEPLOY_PYTHONANYWHERE.md`

No migrations, database schema changes, new dependencies, CSS changes or JavaScript
changes were needed. The private local `.env` was not edited.

## Environment and first local test

Preserve the existing `OPENROUTER_API_KEY` and `CONSENSUS_API_KEY`. Add/set:

```dotenv
OPENROUTER_FREE_TEST_MODE=False
RESEARCH_MODE=balanced
PLANNER_MODEL_1=google/gemini-3.5-flash
PLANNER_MODEL_2=meta-llama/llama-4-scout
PLANNER_MODEL_3=qwen/qwen3.5-27b
PLANNER_MAX_TOKENS=1600
PRIMARY_SYNTHESIZER_MODEL=deepseek/deepseek-v4-flash
ALTERNATIVE_SYNTHESIZER_MODEL=openai/o4-mini
SEMANTIC_SCHOLAR_API_KEY=
SEMANTIC_SCHOLAR_ENABLED=True
SEMANTIC_SCHOLAR_TIMEOUT=30
SEMANTIC_SCHOLAR_MAX_RETRIES=0
CONSENSUS_ENABLED=True
MAX_RETRIEVAL_QUERIES=5
SEMANTIC_SCHOLAR_RESULTS_PER_QUERY=5
CONSENSUS_RESULTS_PER_QUERY=5
SYNTHESIS_MAX_EVIDENCE_ITEMS=8
```

Semantic Scholar's key is optional. The retired `BALANCED_*_MODEL` variables are
ignored for new Balanced runs; Deep settings are unchanged. Existing configured
provider retry/token/timeout overrides remain in effect. Set FREE TEST MODE=True
to restore the earlier free workflow, rather than paid planners.

Stop the local server with Ctrl+C, then run from the project folder:

```powershell
$env:DEBUG="True"
$env:ALLOWED_HOSTS="localhost,127.0.0.1,[::1]"
.\.venv\Scripts\python.exe manage.py runserver
```

Open `http://127.0.0.1:8000/`, start a new Balanced research question, and submit
“Does sleep improve long-term memory in adults?”. This intentional live test incurs
provider usage. Check three planner cards, Semantic Scholar/Consensus badges,
deduplicated paper counts, the final answer and mapped citations. An o4-mini
re-synthesis uses saved evidence and makes one additional paid judge call.

## Validation and spending controls

- **266 tests passed**, including 33 new Plan-and-Solve tests.
- Django system check passed.
- `migrate --check` passed; `makemigrations --check --dry-run` found no changes.
- Both dashboard/theme JavaScript syntax checks passed.
- `collectstatic --noinput` succeeded (131 existing assets unchanged).
- Final automated suite uses mocked provider HTTP and a runner blocking unmocked
  external HTTP. Local browser-endpoint HTTP remains allowed.

Planners have a 1,600-token ceiling each versus the earlier shared 4,096-token
ceiling, deterministic aggregation avoids a fourth planning LLM call, and only
eight deduplicated papers enter synthesis by default. Retrieval is capped at five
queries per provider, five results each, and retries default to zero. Primary
synthesis uses the requested lower-cost DeepSeek model. Actual savings depend on
usage and pricing; multiple Consensus searches can increase retrieval charges.
The synchronous batches can still exceed hosting request limits. No percentage
reduction in total bill or live model-quality claim is asserted.
