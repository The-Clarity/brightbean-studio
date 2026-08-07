from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.core.management import CommandError, call_command
from django.test import override_settings
from django.utils import timezone

from apps.common.health import record_worker_heartbeat, required_services_available, worker_heartbeat_is_fresh


@override_settings(REDIS_URL="redis://redis:6379/0")
@patch("apps.common.health.connection.ensure_connection")
def test_required_services_available_checks_database_and_shared_cache(_ensure_connection):
    stored = {}

    def store(key, value, timeout):
        stored[key] = value

    with (
        patch("apps.common.health.cache.set", side_effect=store),
        patch("apps.common.health.cache.get", side_effect=stored.get),
        patch("apps.common.health.cache.delete", side_effect=lambda key: stored.pop(key, None)),
    ):
        available = required_services_available()

    assert available is True


@override_settings(REDIS_URL="redis://redis:6379/0")
@patch("apps.common.health.connection.ensure_connection", side_effect=RuntimeError("db.internal refused"))
def test_required_services_available_fails_closed(_ensure_connection):
    assert required_services_available() is False


def test_worker_heartbeat_reports_only_recent_worker_activity():
    now = timezone.now()

    record_worker_heartbeat(now=now, worker_id="worker-a")

    assert worker_heartbeat_is_fresh(now=now + timedelta(seconds=30), worker_id="worker-a") is True
    assert worker_heartbeat_is_fresh(now=now + timedelta(seconds=30), worker_id="worker-b") is False
    assert worker_heartbeat_is_fresh(now=now + timedelta(seconds=91), worker_id="worker-a") is False
    cache.clear()


def test_worker_heartbeat_rejects_a_timestamp_from_the_future():
    now = timezone.now()

    record_worker_heartbeat(now=now + timedelta(minutes=5), worker_id="worker-a")

    assert worker_heartbeat_is_fresh(now=now, worker_id="worker-a") is False
    cache.clear()


@override_settings(REDIS_URL="redis://redis:6379/0")
@patch("apps.common.management.commands.worker_healthcheck.required_services_available", return_value=True)
def test_worker_healthcheck_accepts_a_fresh_heartbeat(_services_ready):
    record_worker_heartbeat()

    call_command("worker_healthcheck")


@override_settings(REDIS_URL="redis://redis:6379/0")
@patch("apps.common.management.commands.worker_healthcheck.required_services_available", return_value=True)
def test_worker_healthcheck_rejects_a_missing_heartbeat(_services_ready):
    cache.clear()

    with pytest.raises(CommandError, match="worker unavailable"):
        call_command("worker_healthcheck")
