from django.shortcuts import get_object_or_404, redirect, render
from django.conf import settings
from django.http import HttpResponseBadRequest, HttpResponse, Http404
from django.views.decorators.http import require_GET, require_POST
from uuid import uuid4, UUID
from django.db import DatabaseError
from agents.budgets import budget_for

from research.services import run_research
from agents.security import safe_source_url
from research.models import ResearchQuery
from research.metrics import select_attempts, research_metrics, credentials_ready, provider_key, source_is_valid, succeeded
from research.services import retry_research, ResearchBusy
from research.execution import stale_queries

from .forms import QuestionForm, RatingForm
from .models import Chat
from .services import save_question
from .presentation import prepare_dashboard_result
from research.configuration import select_configuration, allowed_judges, run_models, selected_judge


def dashboard_context(chat=None, form=None):
    queries = list(chat.research_queries.select_related("final_response").prefetch_related("agent_responses").order_by("created_at", "pk")) if chat else []
    for query in queries:
        query.retry_token = uuid4()
        selection = query.execution_data.get("selection") or {}
        query.deep_selection_enabled = selection.get("mode") == "deep" and not selection.get("free_test") and not settings.OPENROUTER_FREE_TEST_MODE
        query.retry_models = [m for m in run_models(query) if budget_for(m["label"].lower())["enabled"]]
        query.judge_models = allowed_judges(query)
        query.selected_judge_id = selected_judge(query)
        query.deep_judge_options = settings.DEEP_SYNTHESIZER_OPTIONS
        query.run_models = run_models(query)
        slot_labels = {m["label"].lower(): m.get("display_label", m["label"]) for m in query.run_models}
        current_provider = query.execution_data.get("current_provider", "")
        query.current_provider_display = slot_labels.get(current_provider, current_provider.capitalize())
        query.latest_rating = query.ratings.order_by("-pk").first()
        query.rating_form = RatingForm()
        query.is_stale = query.status == "processing" and stale_queries().filter(pk=query.pk).exists()
        responses = list(query.agent_responses.all())
        latest, active = select_attempts(query)
        displayed = {**latest, **active}
        query.ai_responses = [response for response in displayed.values() if response.provider != "consensus"]
        query.academic_responses = [response for response in displayed.values() if response.provider == "consensus"]
        query.attempt_history = sorted(responses, key=lambda r: r.pk, reverse=True)
        query.latest_failures = [r for r in latest.values() if not succeeded(r)]
        for response in query.attempt_history:
            key = provider_key(response)
            response.is_latest = latest[key].pk == response.pk
            response.is_active = key in active and active[key].pk == response.pk
            response.is_displayed = displayed[key].pk == response.pk
            for collection in ("papers", "citations"):
                for item in response.normalized_response.get(collection, []):
                    item["url"] = safe_source_url(item.get("url", ""))
        query.judge_history = list(query.synthesis_attempts.order_by("-pk"))
        for attempt in query.judge_history:
            attempt.judge_label = next((o["label"] for o in settings.DEEP_SYNTHESIZER_OPTIONS if o["id"] == attempt.model_name), attempt.model_name)
            for source in attempt.synthesis_data.get("sources", []):
                source["url"] = safe_source_url(source.get("url", ""))
        query.metrics = research_metrics(query)
        query.final_result = getattr(query, "final_response", None)
        query.synthesis_failure = next((attempt.synthesis_data for attempt in query.judge_history[:1] if attempt.synthesis_data.get("error")), {})
        data = query.final_result.synthesis_data if query.final_result else {}
        query.final_sources = [
            {**source, "url": safe_source_url(source.get("url", ""))}
            for source in data.get("sources", []) if source.get("source_id") in data.get("source_ids", []) and source_is_valid(query, source)
        ]
        registry = {s["source_id"]: {**s, "url": safe_source_url(s.get("url", ""))} for s in data.get("sources", []) if source_is_valid(query, s)}
        for finding in data.get("key_findings", []):
            finding["mapped_sources"] = [registry.get(sid, {"source_id": sid, "title": "Invalid source mapping — manual review required"}) for sid in finding.get("supporting_source_ids", [])]
        query.synthesis_stale = bool(query.final_result and query.final_result.answer and "evidence_attempt_ids" in data and data["evidence_attempt_ids"] != sorted(r.pk for r in active.values()))
        prepare_dashboard_result(query, active, registry)
    form = form if form is not None else QuestionForm()
    return {
        "chats": Chat.objects.all(),
        "selected_chat": chat,
        "chat_messages": chat.messages.all() if chat else [],
        "research_queries": queries,
        "form": form,
        "show_deep_selector": form["research_mode"].value() == "deep" and not settings.OPENROUTER_FREE_TEST_MODE,
        "deep_synthesizer_options": settings.DEEP_SYNTHESIZER_OPTIONS,
        "diagnostics_enabled": settings.RESEARCH_DIAGNOSTICS_ENABLED,
        "credential_readiness": credentials_ready(),
        "judge_models": settings.SYNTHESIZER_MODELS,
        "retry_models": [m for m in settings.OPENROUTER_MODELS if budget_for(m["label"].lower())["enabled"]],
        "disabled_providers": [m.get("display_label", m["label"]) for m in settings.OPENROUTER_MODELS if not budget_for(m["label"].lower())["enabled"]] + [key for key in ("Consensus", "Synthesis") if not budget_for(key.lower())["enabled"]],
        "consensus_enabled": budget_for("consensus")["enabled"],
        "synthesis_enabled": budget_for("synthesis")["enabled"],
        "free_test_mode": settings.OPENROUTER_FREE_TEST_MODE,
        "configured_models": settings.OPENROUTER_MODELS,
        "configured_judge": settings.SYNTHESIZER_MODEL,
    }


@require_GET
def dashboard(request, chat_id=None):
    chat = get_object_or_404(Chat, pk=chat_id) if chat_id is not None else None
    return render(request, "dashboard.html", dashboard_context(chat))


@require_POST
def new_chat(request):
    chat = Chat.objects.create()
    return redirect("chats:detail", chat_id=chat.pk)


@require_POST
def rename_chat(request, chat_id):
    chat = get_object_or_404(Chat, pk=chat_id)
    title = request.POST.get("title", "").strip()
    if not title or len(title) > 200:
        return HttpResponseBadRequest("Use a title between 1 and 200 characters.")
    chat.title = title
    chat.save(update_fields=["title", "updated_at"])
    return redirect("chats:detail", chat_id=chat.pk)


@require_POST
def delete_chat(request, chat_id):
    chat = get_object_or_404(Chat, pk=chat_id)
    if request.POST.get("confirm_chat") != str(chat.pk):
        return HttpResponseBadRequest("Confirm the selected chat before deleting it.")
    chat.delete()
    return redirect("chats:dashboard")


@require_POST
def clear_chats(request):
    if request.POST.get("confirmation") != "DELETE ALL CHATS":
        return HttpResponseBadRequest("Type DELETE ALL CHATS to confirm.")
    Chat.objects.all().delete()
    return redirect("chats:dashboard")


@require_POST
def submit_question(request, chat_id=None):
    chat = get_object_or_404(Chat, pk=chat_id) if chat_id is not None else None
    form = QuestionForm(request.POST)
    if not form.is_valid():
        return render(request, "dashboard.html", dashboard_context(chat, form), status=400)
    try:
        selection = select_configuration(form.cleaned_data["research_mode"], form.cleaned_data.get("deep_synthesizer"))
        query = save_question(form.cleaned_data["question"], chat, form.cleaned_data.get("submission_key"), selection=selection)
    except ValueError:
        return HttpResponseBadRequest("Submission token conflict. Refresh the page before starting a different question.")
    except DatabaseError:
        return HttpResponse("Research is busy. Refresh the chat to check whether your request was saved.", status=503)
    try:
        run_research(query)
    except DatabaseError:
        return HttpResponse("Research was saved, but execution is busy. Refresh this chat to inspect its state.", status=503)
    return redirect("chats:detail", chat_id=query.chat_id)


@require_POST
def retry(request, query_id):
    query = get_object_or_404(ResearchQuery, pk=query_id)
    try:
        key = UUID(request.POST["action_key"]) if request.POST.get("action_key") else None
        model_id = request.POST.get("judge_model") or None
        selection = query.execution_data.get("selection") or {}
        if selection.get("mode") == "deep":
            if model_id:
                raise ValueError("Deep synthesis accepts only a configured choice key.")
            judge_key = request.POST.get("deep_synthesizer")
            if judge_key:
                if settings.OPENROUTER_FREE_TEST_MODE or selection.get("free_test"):
                    raise ValueError("Premium judge selection is disabled in free mode.")
                model_id = select_configuration("deep", judge_key)["judge_model"]
        elif request.POST.get("deep_synthesizer"):
            raise ValueError("Deep judge selection requires a Deep run.")
        retry_research(query, request.POST.get("target", ""), model_id=model_id, action_key=key)
    except ValueError:
        return HttpResponseBadRequest("Invalid retry target or judge model.")
    except ResearchBusy:
        return HttpResponse("Research is already processing. Refresh later.", status=409)
    except DatabaseError:
        return HttpResponse("Research is busy. Refresh to inspect the current execution.", status=503)
    return redirect("chats:detail", chat_id=query.chat_id)


@require_GET
def evaluations(request):
    from research.evaluation import load_cases
    if not settings.RESEARCH_DIAGNOSTICS_ENABLED:
        raise Http404
    search = request.GET.get("question", "").strip()[:2000]
    results = list(ResearchQuery.objects.filter(question__icontains=search).prefetch_related("agent_responses", "ratings").order_by("-pk")[:100])
    for query in results:
        query.metrics = research_metrics(query)
        query.models_used = list(dict.fromkeys(query.agent_responses.filter(provider="openrouter").values_list("model_name", flat=True)))
        query.judge_used = query.synthesis_attempts.order_by("-pk").first()
        query.latest_rating = query.ratings.order_by("-pk").first()
    cases = [{**case, "submission_key": uuid4()} for case in load_cases()]
    return render(request, "research/evaluations.html", {"cases": cases, "results": results, "question_filter": search})


@require_POST
def run_evaluation(request, case_id):
    from research.evaluation import load_cases
    if not settings.RESEARCH_DIAGNOSTICS_ENABLED:
        raise Http404
    cases = load_cases()
    if not 0 <= case_id < len(cases):
        raise Http404
    try:
        key = UUID(request.POST["submission_key"]) if request.POST.get("submission_key") else None
        query = save_question(cases[case_id]["question"], submission_key=key)
    except ValueError:
        return HttpResponseBadRequest("Invalid or reused submission token.")
    except DatabaseError:
        return HttpResponse("Research is busy. Refresh before trying again.", status=503)
    query.evaluation_case = cases[case_id]
    query.save(update_fields=["evaluation_case"])
    try:
        run_research(query)
    except DatabaseError:
        return HttpResponse("Research was saved, but execution is busy. Refresh before trying again.", status=503)
    return redirect("chats:detail", chat_id=query.chat_id)


@require_POST
def rate_research(request, query_id):
    if not settings.RESEARCH_DIAGNOSTICS_ENABLED:
        raise Http404
    query = get_object_or_404(ResearchQuery, pk=query_id)
    if query.status not in {"completed", "failed"}:
        return HttpResponse("Wait for research to finish before rating it.", status=409)
    final = getattr(query, "final_response", None)
    active_id = final.active_attempt_id if final else None
    if request.POST.get("synthesis_attempt", "") != (str(active_id) if active_id else ""):
        return HttpResponse("The active answer changed. Refresh before rating it.", status=409)
    form = RatingForm(request.POST)
    if not form.is_valid():
        return HttpResponseBadRequest("All four ratings must be integers from 1 to 5; notes must be at most 5000 characters.")
    rating = form.save(commit=False)
    rating.research_query = query
    rating.synthesis_attempt_id = active_id
    rating.save()
    return redirect("chats:detail", chat_id=query.chat_id)
