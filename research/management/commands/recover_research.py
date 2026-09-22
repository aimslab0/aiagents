from django.core.management.base import BaseCommand, CommandError

from research.execution import stale_queries, ResearchBusy
from research.recovery import recovery_plan, recover_query


class Command(BaseCommand):
    help = "Report stale processing research. Recovery API requests require --live."

    def add_arguments(self, parser):
        parser.add_argument("--live", action="store_true")
        parser.add_argument("--query-id", type=int)
        parser.add_argument("--skip-synthesis", action="store_true")

    def handle(self, *args, **options):
        queries = stale_queries().order_by("pk")
        if options["query_id"] is not None:
            queries = queries.filter(pk=options["query_id"])
        failures = False
        count = 0
        for query in queries.iterator():
            count += 1
            plan = recovery_plan(query, not options["skip_synthesis"])
            self.stdout.write(f"Query {query.pk}: missing/failed steps={','.join(plan['targets']) or 'none'}; synthesis={'yes' if plan['synthesis'] else 'no'}")
            if options["live"]:
                try:
                    recovered = recover_query(query, not options["skip_synthesis"])
                    self.stdout.write(f"Query {query.pk}: {recovered.stage}")
                    failures |= recovered.stage != "COMPLETED"
                except ResearchBusy:
                    self.stdout.write(f"Query {query.pk}: skipped; no longer stale")
                except Exception:
                    failures = True
                    self.stdout.write(f"Query {query.pk}: recovery unavailable; saved attempts are preserved")
        self.stdout.write(f"{count} recoverable query/queries inspected. " + ("Live recovery requested." if options["live"] else "Dry run: no database changes or network requests."))
        if failures:
            raise CommandError("Some recovery steps remain incomplete. Review saved diagnostics.")
