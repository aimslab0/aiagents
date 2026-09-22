from django.db import migrations


def backfill(apps, schema_editor):
    Query = apps.get_model("research", "ResearchQuery")
    alias = schema_editor.connection.alias
    Query.objects.using(alias).filter(status="failed").update(stage="FAILED")
    Query.objects.using(alias).filter(status="processing").update(stage="COLLECTING_AGENTS")
    for query in Query.objects.using(alias).filter(status="completed").iterator():
        has_failure = any((r.normalized_response or {}).get("error") for r in query.agent_responses.all())
        has_final = query.synthesis_attempts.filter(succeeded=True).exists()
        query.stage = "PARTIAL" if has_failure or not has_final else "COMPLETED"
        query.save(using=alias, update_fields=["stage"])


class Migration(migrations.Migration):
    dependencies = [("research", "0004_researchquery_execution_data_and_more")]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
