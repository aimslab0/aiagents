from django.db import migrations


def preserve(apps, schema_editor):
    Agent = apps.get_model("research", "AgentResponse")
    Final = apps.get_model("research", "FinalResponse")
    Attempt = apps.get_model("research", "SynthesisAttempt")
    alias = schema_editor.connection.alias
    for response in Agent.objects.using(alias).all().iterator():
        data = response.normalized_response or {}
        response.provider_key = "consensus" if response.provider == "consensus" else data.get("label", response.model_name).lower()
        response.succeeded = not bool(data.get("error"))
        if response.provider == "consensus":
            response.paper_count = len(data.get("papers", []))
            response.citations_used.set(response.citations.all())
        response.save(using=alias, update_fields=["provider_key", "succeeded", "paper_count"])
    for final in Final.objects.using(alias).all().iterator():
        data = dict(final.synthesis_data or {})
        failure = data.pop("last_attempt_error", None)
        attempt = Attempt.objects.using(alias).create(
            research_query_id=final.research_query_id, model_name=data.get("model", "unknown"),
            answer=final.answer, confidence=final.confidence, synthesis_data=data,
            succeeded=data.get("status") == "completed",
        )
        Attempt.objects.using(alias).filter(pk=attempt.pk).update(created_at=final.created_at)
        if attempt.succeeded:
            final.active_attempt_id = attempt.pk
            final.save(using=alias, update_fields=["active_attempt"])
        if failure:
            Attempt.objects.using(alias).create(research_query_id=final.research_query_id,
                model_name=failure.get("model", "unknown"), synthesis_data=failure, succeeded=False)


class Migration(migrations.Migration):
    dependencies = [("research", "0002_agentresponse_citations_used_and_more")]
    operations = [migrations.RunPython(preserve, migrations.RunPython.noop)]
