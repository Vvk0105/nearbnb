from app.jobs.celery_app import ping
from tests.helpers import all_routes


def test_health_route_registered():
    paths = {path for _, path in all_routes()}
    assert "/api/v1/health" in paths


def test_celery_ping_task():
    assert ping() == "pong"


async def test_monitors_can_check_the_health_links_with_head(client):
    """UptimeRobot and similar services use HEAD. A 405 here makes a healthy site look down."""
    for path in ("/api/v1/health", "/api/v1/health/ready"):
        r = await client.head(path)
        assert r.status_code == 200, (path, r.status_code)
        assert (await client.get(path)).status_code == 200
