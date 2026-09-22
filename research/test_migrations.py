from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class AttemptMigrationTests(TransactionTestCase):
    def test_existing_results_survive_upgrade_without_invented_metrics(self):
        executor = MigrationExecutor(connection)
        restore_targets = executor.loader.graph.leaf_nodes()
        original = [("research", "0001_initial")]
        latest = [("research", "0003_preserve_existing_attempts")]
        executor.migrate(original)
        try:
            apps = executor.loader.project_state(original).apps
            chat = apps.get_model("chats", "Chat").objects.create(title="Existing chat")
            query = apps.get_model("research", "ResearchQuery").objects.create(chat_id=chat.pk, question="Existing question")
            agent = apps.get_model("research", "AgentResponse").objects.create(research_query_id=query.pk, provider="consensus", model_name="search-v1", raw_response="{}", normalized_response={"error": None, "papers": [{"title": "Existing paper"}]})
            citation = apps.get_model("research", "Citation").objects.create(research_query_id=query.pk, agent_response_id=agent.pk, title="Existing paper", url="https://example.org/paper", source_name="Journal")
            final = apps.get_model("research", "FinalResponse").objects.create(research_query_id=query.pk, answer="Existing answer", synthesis_data={"status": "completed", "model": "existing/judge", "last_attempt_error": {"status": "failed", "model": "existing/judge", "error": {"code": "timeout"}}})
            executor = MigrationExecutor(connection)
            executor.migrate(latest)
            apps = executor.loader.project_state(latest).apps
            saved = apps.get_model("research", "FinalResponse").objects.get(pk=final.pk)
            attempts = apps.get_model("research", "SynthesisAttempt").objects.filter(research_query_id=query.pk)
            self.assertEqual(saved.answer, "Existing answer")
            self.assertEqual(attempts.count(), 2)
            self.assertTrue(attempts.get(pk=saved.active_attempt_id).succeeded)
            self.assertIsNone(attempts.get(pk=saved.active_attempt_id).estimated_cost)
            upgraded = apps.get_model("research", "AgentResponse").objects.get(pk=agent.pk)
            self.assertEqual(list(upgraded.citations_used.values_list("pk", flat=True)), [citation.pk])
            self.assertIsNone(upgraded.latency_ms)
            self.assertEqual(upgraded.provider_key, "consensus")
        finally:
            MigrationExecutor(connection).migrate(restore_targets)
