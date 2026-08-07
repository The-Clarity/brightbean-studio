from django.core.management.base import BaseCommand, CommandError

from apps.common.health import required_services_available, worker_heartbeat_is_fresh


class Command(BaseCommand):
    help = "Fail unless the worker heartbeat and its required services are healthy."

    def handle(self, *args, **options):
        if not required_services_available() or not worker_heartbeat_is_fresh():
            raise CommandError("worker unavailable")

        self.stdout.write("ok")
