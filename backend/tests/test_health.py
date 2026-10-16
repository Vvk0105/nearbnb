from app.jobs.celery_app import ping
from app.main import app


def test_health_route_registered():
    paths = {route.path for route in app.routes}
    assert "/api/v1/health" in paths


def test_celery_ping_task():
    assert ping() == "pong"
