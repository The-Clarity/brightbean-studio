import socket
from datetime import datetime, timedelta
from uuid import uuid4

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.utils import timezone

WORKER_HEARTBEAT_KEY_PREFIX = "brightbean:worker:heartbeat"
WORKER_HEARTBEAT_TTL_SECONDS = 120
WORKER_READY_WINDOW = timedelta(seconds=90)


def required_services_available() -> bool:
    if not settings.REDIS_URL:
        return False

    probe_key = f"brightbean:health:{uuid4().hex}"
    probe_value = uuid4().hex

    try:
        connection.ensure_connection()
        cache.set(probe_key, probe_value, timeout=10)
        cache_value = cache.get(probe_key)
        cache.delete(probe_key)
    except Exception:
        return False

    return cache_value == probe_value


def worker_heartbeat_key(*, worker_id: str | None = None) -> str:
    identity = worker_id or socket.gethostname()
    return f"{WORKER_HEARTBEAT_KEY_PREFIX}:{identity}"


def record_worker_heartbeat(*, now: datetime | None = None, worker_id: str | None = None) -> None:
    observed_at = now or timezone.now()
    cache.set(worker_heartbeat_key(worker_id=worker_id), observed_at.isoformat(), timeout=WORKER_HEARTBEAT_TTL_SECONDS)


def worker_heartbeat_is_fresh(*, now: datetime | None = None, worker_id: str | None = None) -> bool:
    value = cache.get(worker_heartbeat_key(worker_id=worker_id))
    if not isinstance(value, str):
        return False

    try:
        observed_at = datetime.fromisoformat(value)
    except ValueError:
        return False

    current_time = now or timezone.now()
    heartbeat_age = current_time - observed_at
    return timedelta(0) <= heartbeat_age <= WORKER_READY_WINDOW
