from app.jobs.celery_app import ping
from tests.helpers import all_routes


def test_health_route_registered():
    paths = {path for _, path in all_routes()}
    assert "/api/v1/health" in paths


def test_celery_ping_task():
    assert ping() == "pong"
