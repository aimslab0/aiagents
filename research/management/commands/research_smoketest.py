from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from agents.consensus import ConsensusClient, STUDY_TYPES
from agents.openrouter import OpenRouterClient
from research.evaluation import load_cases
from research.metrics import ExecutionTimer, credentials_ready


class Command(BaseCommand):
    help = "Check local research configuration; network calls require --live. Never prints credentials or response text."

    def add_arguments(self, parser):
        parser.add_argument("--live", action="store_true", help="Spend API credits on small requests (no saved research or synthesis).")
        parser.add_argument("--provider", choices=["both", "openrouter", "consensus"], default="both")

    def handle(self, *args, **options):
        failed = False
        readiness = credentials_ready()
        for name, ready in readiness.items():
            self.stdout.write(f"{name}: {'Configured' if ready else 'Missing'}")
        try:
            executor = MigrationExecutor(connection)
            if executor.migration_plan(executor.loader.graph.leaf_nodes()):
                raise ValueError
            load_cases()
            if not settings.OPENROUTER_MODELS or not all(m.get("id") and m.get("label") for m in settings.OPENROUTER_MODELS):
                raise ValueError
            if settings.SYNTHESIZER_MODEL not in settings.SYNTHESIZER_MODELS or not set(settings.CONSENSUS_STUDY_TYPES) <= STUDY_TYPES:
                raise ValueError
            self.stdout.write("PASS local database, migrations, model configuration and evaluation dataset")
        except Exception:
            raise CommandError("FAIL local checks. Run check and migrate, then review configuration.") from None
        if not options["live"]:
            self.stdout.write("PASS offline smoke check: no network requests made. Missing keys must be configured before live testing.")
            return
        self.stdout.write("Live mode: up to one OpenRouter request (512 output tokens) and one Consensus request (one paper).")
        for provider in ("openrouter", "consensus"):
            if options["provider"] not in ("both", provider):
                continue
            name = "OpenRouter" if provider == "openrouter" else "Consensus"
            if not readiness[name]:
                self.stdout.write(f"FAIL {name}: credential missing; request skipped")
                failed = True
                continue
            timer = ExecutionTimer()
            try:
                if provider == "openrouter":
                    model = settings.OPENROUTER_MODELS[0]["id"]
                    result = OpenRouterClient(max_tokens=512).research("Briefly explain how sleep relates to memory. Use at most one key finding and no citations if unsure.", model)
                else:
                    result = ConsensusClient(page_size=1).search("sleep and memory consolidation")
                if result.get("error"):
                    raise ValueError
                self.stdout.write(f"PASS {name}: response validated; latency_ms={timer.finish(True)['latency_ms']}")
            except Exception:
                failed = True
                self.stdout.write(f"FAIL {name}: request or response validation failed; review credentials, service availability, credits and model configuration")
        if failed:
            raise CommandError("Live smoke test had failures. No credentials or response bodies were printed.")
