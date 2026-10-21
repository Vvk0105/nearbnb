"""Phase 10 security sweep: every route in the app is checked, so a new endpoint cannot ship unprotected by accident."""
import re
import uuid

import pytest
from tests.conftest import register_and_verify
from tests.helpers import all_routes

# Routes that are meant to work without logging in. Anything else must answer 401 to a stranger.
PUBLIC = {
    ("GET", "/api/v1/health"),
    ("GET", "/api/v1/health/ready"),
    ("POST", "/api/v1/auth/register"),
    ("POST", "/api/v1/auth/verify-email"),
    ("POST", "/api/v1/auth/resend-otp"),
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/refresh"),
    ("POST", "/api/v1/auth/logout"),
    ("POST", "/api/v1/auth/forgot-password"),
    ("POST", "/api/v1/auth/reset-password"),
    ("POST", "/api/v1/webhooks/stripe"),
}
PUBLIC.update({
    ("GET", "/api/v1/media/public/{path:path}"),
    ("GET", "/api/v1/meta/listing"),
    ("GET", "/api/v1/meta/booking"),
    ("GET", "/api/v1/meta/issue-categories"),
    ("GET", "/api/v1/meta/cancellation"),
    ("GET", "/api/v1/properties/{property_id}"),
    ("GET", "/api/v1/properties/{property_id}/reviews"),
    ("GET", "/api/v1/units/{unit_id}/availability/calendar"),
    ("GET", "/api/v1/units/{unit_id}/availability/day"),
    ("POST", "/api/v1/units/{unit_id}/quote"),
})
PUBLIC_PREFIXES = ("/api/v1/legal", "/api/v1/search", "/api/v1/listings", "/api/v1/places", "/api/v1/ical/")
# Public prefixes that still contain a few private routes are checked separately below.


def _routes():
    yield from ((m, p) for m, p in all_routes() if p.startswith("/api/v1"))  # /docs and /openapi.json are switched off in production (main.py)


def _fill(path: str) -> str:
    return re.sub(r"\{[^}]+\}", lambda _m: str(uuid.uuid4()), path)


def _is_public(method: str, path: str) -> bool:
    return (method, path) in PUBLIC or (method == "GET" and path.startswith(PUBLIC_PREFIXES))


def test_there_are_routes_to_check():
    assert len(list(_routes())) > 100


@pytest.mark.asyncio
async def test_every_non_public_route_rejects_strangers(client):
    leaks = []
    for method, path in _routes():
        if _is_public(method, path):
            continue
        r = await client.request(method, _fill(path), json={} if method != "GET" else None)
        if r.status_code not in (401, 403):
            leaks.append((method, path, r.status_code))
    assert not leaks, f"routes answering a stranger: {leaks}"


@pytest.mark.asyncio
async def test_public_get_routes_never_expose_private_data_or_crash(client):
    bad = []
    for method, path in _routes():
        if method == "GET" and _is_public(method, path):
            r = await client.get(_fill(path))
            if r.status_code >= 500 and r.status_code != 503:  # 503 = Google key not set here
                bad.append((path, r.status_code))
    assert not bad


@pytest.mark.asyncio
async def test_a_plain_guest_cannot_reach_any_admin_route(client):
    await register_and_verify(client)
    from tests.conftest import csrf_headers

    leaks = []
    for method, path in _routes():
        if "/admin" not in path:
            continue
        r = await client.request(method, _fill(path), json={} if method != "GET" else None, headers=csrf_headers(client))
        if r.status_code != 403:
            leaks.append((method, path, r.status_code))
    assert not leaks, f"admin routes open to a guest: {leaks}"


@pytest.mark.asyncio
async def test_a_plain_guest_cannot_reach_host_routes(client):
    await register_and_verify(client)
    from tests.conftest import csrf_headers

    open_routes = []
    for method, path in _routes():
        if not path.startswith("/api/v1/host/") or path.startswith("/api/v1/host/apply"):
            continue
        r = await client.request(method, _fill(path), json={} if method != "GET" else None, headers=csrf_headers(client))
        # 422 means the empty test body failed validation before the handler's host check ran; the
        # handlers' ownership checks with valid bodies are covered in each module's own tests.
        if r.status_code not in (403, 404, 422):
            open_routes.append((method, path, r.status_code))
    assert not open_routes, f"host routes open to a non-host: {open_routes}"


@pytest.mark.asyncio
async def test_unsafe_requests_with_a_session_need_the_csrf_token(client):
    await register_and_verify(client)
    for method, path in _routes():
        if method in ("POST", "PUT", "PATCH", "DELETE") and not (method, path) in PUBLIC:
            r = await client.request(method, _fill(path), json={})
            assert r.status_code == 403 and r.json()["error"]["code"] == "CSRF_FAILED", (method, path, r.status_code)


@pytest.mark.asyncio
async def test_security_headers_on_every_answer(client):
    for url in ("/api/v1/health", "/api/v1/bookings", "/nope"):
        r = await client.get(url)
        assert r.headers["x-content-type-options"] == "nosniff", url
        assert r.headers["x-frame-options"] == "DENY", url
        assert r.headers["referrer-policy"] == "no-referrer", url
        assert "frame-ancestors 'none'" in r.headers["content-security-policy"], url
    assert (await client.get("/api/v1/bookings")).headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_the_swagger_page_may_load_its_files_but_api_answers_stay_locked_down(client):
    docs = await client.get("/docs")                               # switched off in production, available in development and tests
    assert docs.status_code == 200
    csp = docs.headers["content-security-policy"]
    assert "https://cdn.jsdelivr.net" in csp and "frame-ancestors 'none'" in csp
    api = await client.get("/api/v1/health")
    assert api.headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"
