from unittest.mock import patch

from django.urls import reverse


@patch("apps.accounts.views.required_services_available", return_value=True)
def test_health_reports_ready_without_dependency_details(_services_ready, client):
    response = client.get(reverse("health_check"))

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@patch("apps.accounts.views.required_services_available", return_value=False)
def test_health_fails_closed_without_dependency_details(_services_ready, client):
    response = client.get(reverse("health_check"))

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
