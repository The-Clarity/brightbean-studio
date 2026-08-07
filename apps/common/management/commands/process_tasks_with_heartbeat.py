import threading

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import close_old_connections

from apps.common.health import record_worker_heartbeat

HEARTBEAT_INTERVAL_SECONDS = 30


class Command(BaseCommand):
    help = "Run background tasks while publishing a shared worker heartbeat."

    def handle(self, *args, **options):
        stopped = threading.Event()

        def publish_heartbeat() -> None:
            while not stopped.is_set():
                close_old_connections()
                record_worker_heartbeat()
                stopped.wait(HEARTBEAT_INTERVAL_SECONDS)

        heartbeat_thread = threading.Thread(target=publish_heartbeat, name="worker-heartbeat", daemon=True)
        heartbeat_thread.start()

        try:
            call_command("process_tasks")
        finally:
            stopped.set()
            heartbeat_thread.join(timeout=HEARTBEAT_INTERVAL_SECONDS)
