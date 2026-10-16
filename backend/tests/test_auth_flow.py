from sqlalchemy import select, text

from app.modules.audit.models import AuditLog
from tests.conftest import PASSWORD, csrf_headers, latest_code, register_and_verify

API = "/api/v1"


async def test_register_verify_login_me_logout(client):
    user = await register_and_verify(client)
    assert user["email"] == "guest@example.com" and user["email_verified"] is True
    assert user["pending_consents"] == []  # accepted current Terms and Privacy at signup

    me = await client.get(f"{API}/me")
    assert me.status_code == 200 and me.json()["full_name"] == "Test Guest"

    out = await client.post(f"{API}/auth/logout", headers=csrf_headers(client))
    assert out.status_code == 200
    assert (await client.get(f"{API}/me")).status_code == 401

    login = await client.post(f"{API}/auth/login", json={"email": "guest@example.com", "password": PASSWORD})
    assert login.status_code == 200
    assert (await client.get(f"{API}/me")).status_code == 200


async def test_second_user_can_log_in_in_same_browser_and_logout_ends_session(client):
    await register_and_verify(client, email="first@example.com")
    assert (await client.get(f"{API}/me")).json()["email"] == "first@example.com"
    first_cookie = client.cookies.get("access_token")

    # Browser still holds the first user's cookies and logs in as someone else (no logout in between).
    client.cookies.clear()  # register_and_verify below uses a fresh client state per user in real browsers
    await register_and_verify(client, email="second@example.com")
    assert (await client.get(f"{API}/me")).json()["email"] == "second@example.com"
    assert client.cookies.get("access_token") != first_cookie

    # Logging in again while still logged in switches the session to the new user.
    other = await client.post(f"{API}/auth/login", json={"email": "first@example.com", "password": PASSWORD},
                              headers=csrf_headers(client))
    assert other.status_code == 200
    assert (await client.get(f"{API}/me")).json()["email"] == "first@example.com"

    out = await client.post(f"{API}/auth/logout", headers=csrf_headers(client))
    assert out.status_code == 200
    assert client.cookies.get("access_token") is None and client.cookies.get("csrf_token") is None
    assert (await client.get(f"{API}/me")).status_code == 401
    assert (await client.post(f"{API}/auth/refresh", headers=csrf_headers(client))).status_code == 401


async def test_register_requires_terms_and_strong_password(client):
    r = await client.post(f"{API}/auth/register", json={
        "email": "a@example.com", "password": PASSWORD, "full_name": "A", "accept_terms": False})
    assert r.status_code == 422 and r.json()["error"]["code"] == "TERMS_REQUIRED"
    r = await client.post(f"{API}/auth/register", json={
        "email": "a@example.com", "password": "short", "full_name": "A", "accept_terms": True})
    assert r.status_code == 422 and r.json()["error"]["code"] == "WEAK_PASSWORD"


async def test_registering_existing_verified_email_does_not_reveal_it(client):
    await register_and_verify(client)
    client.cookies.clear()
    r = await client.post(f"{API}/auth/register", json={
        "email": "guest@example.com", "password": "Another-pass-123", "full_name": "X", "accept_terms": True})
    assert r.status_code == 202  # same response as a brand new email


async def test_login_blocked_until_email_verified(client):
    await client.post(f"{API}/auth/register", json={
        "email": "new@example.com", "password": PASSWORD, "full_name": "N", "accept_terms": True})
    r = await client.post(f"{API}/auth/login", json={"email": "new@example.com", "password": PASSWORD})
    assert r.status_code == 403 and r.json()["error"]["code"] == "EMAIL_NOT_VERIFIED"


async def test_wrong_otp_attempts_lock_the_code(client):
    await client.post(f"{API}/auth/register", json={
        "email": "otp@example.com", "password": PASSWORD, "full_name": "O", "accept_terms": True})
    real = await latest_code("otp@example.com")
    wrong = "000000" if real != "000000" else "111111"
    for _ in range(5):
        r = await client.post(f"{API}/auth/verify-email", json={"email": "otp@example.com", "code": wrong})
        assert r.json()["error"]["code"] == "INVALID_CODE"
    r = await client.post(f"{API}/auth/verify-email", json={"email": "otp@example.com", "code": wrong})
    assert r.json()["error"]["code"] == "CODE_LOCKED"
    # even the correct code no longer works after lock-out
    r = await client.post(f"{API}/auth/verify-email", json={"email": "otp@example.com", "code": real})
    assert r.status_code == 400


async def test_account_locks_after_repeated_bad_passwords(client):
    await register_and_verify(client)
    client.cookies.clear()
    for _ in range(5):
        r = await client.post(f"{API}/auth/login", json={"email": "guest@example.com", "password": "nope-nope-nope"})
        assert r.status_code == 401
    r = await client.post(f"{API}/auth/login", json={"email": "guest@example.com", "password": PASSWORD})
    assert r.status_code == 429 and r.json()["error"]["code"] == "ACCOUNT_LOCKED"


async def test_password_reset_flow_revokes_sessions(client):
    await register_and_verify(client)
    client.cookies.clear()
    r = await client.post(f"{API}/auth/forgot-password", json={"email": "guest@example.com"})
    assert r.status_code == 202
    code = await latest_code("guest@example.com")
    new_pw = "Brand-new-pass-99"
    r = await client.post(f"{API}/auth/reset-password", json={"email": "guest@example.com", "code": code, "new_password": new_pw})
    assert r.status_code == 200
    assert (await client.post(f"{API}/auth/login", json={"email": "guest@example.com", "password": PASSWORD})).status_code == 401
    assert (await client.post(f"{API}/auth/login", json={"email": "guest@example.com", "password": new_pw})).status_code == 200


async def test_forgot_password_unknown_email_gives_same_response(client):
    r = await client.post(f"{API}/auth/forgot-password", json={"email": "nobody@example.com"})
    assert r.status_code == 202


async def test_refresh_rotates_and_reuse_kills_session(client):
    await register_and_verify(client)
    old_refresh = client.cookies.get("refresh_token", path="/api/v1/auth")
    r = await client.post(f"{API}/auth/refresh", headers=csrf_headers(client))
    assert r.status_code == 200
    new_refresh = client.cookies.get("refresh_token", path="/api/v1/auth")
    assert new_refresh != old_refresh
    # replaying the rotated (old) token must fail and revoke the family
    client.cookies.set("refresh_token", old_refresh, path="/api/v1/auth")
    assert (await client.post(f"{API}/auth/refresh", headers=csrf_headers(client))).status_code == 401
    client.cookies.set("refresh_token", new_refresh, path="/api/v1/auth")
    assert (await client.post(f"{API}/auth/refresh", headers=csrf_headers(client))).status_code == 401


async def test_csrf_required_for_authenticated_writes(client):
    await register_and_verify(client)
    r = await client.patch(f"{API}/me", json={"full_name": "Changed"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "CSRF_FAILED"
    r = await client.patch(f"{API}/me", json={"full_name": "Changed"}, headers=csrf_headers(client))
    assert r.status_code == 200 and r.json()["full_name"] == "Changed"


async def test_change_password_requires_current_password(client):
    await register_and_verify(client)
    h = csrf_headers(client)
    r = await client.post(f"{API}/auth/change-password", json={"current_password": "wrong", "new_password": "Another-pass-123"}, headers=h)
    assert r.status_code == 400
    r = await client.post(f"{API}/auth/change-password", json={"current_password": PASSWORD, "new_password": "Another-pass-123"}, headers=h)
    assert r.status_code == 200


async def test_suspended_user_is_rejected(client, db):
    await register_and_verify(client)
    await db.execute(text("UPDATE users SET status='suspended'"))
    await db.commit()
    r = await client.get(f"{API}/me")
    assert r.status_code == 403 and r.json()["error"]["code"] == "ACCOUNT_SUSPENDED"


async def test_deactivated_account_cannot_log_in(client):
    await register_and_verify(client)
    r = await client.post(f"{API}/me/deactivate", json={"password": PASSWORD}, headers=csrf_headers(client))
    assert r.status_code == 200
    client.cookies.clear()
    r = await client.post(f"{API}/auth/login", json={"email": "guest@example.com", "password": PASSWORD})
    assert r.status_code == 401


async def test_admin_two_factor_flow(client, make_admin):
    import pyotp

    await make_admin()
    r = await client.post(f"{API}/auth/login", json={"email": "admin@example.com", "password": PASSWORD})
    assert r.status_code == 200
    h = csrf_headers(client)
    secret = (await client.post(f"{API}/auth/2fa/setup", headers=h)).json()["secret"]
    assert (await client.post(f"{API}/auth/2fa/enable", json={"code": "000000"}, headers=h)).status_code == 400
    assert (await client.post(f"{API}/auth/2fa/enable", json={"code": pyotp.TOTP(secret).now()}, headers=h)).status_code == 200

    client.cookies.clear()
    r = await client.post(f"{API}/auth/login", json={"email": "admin@example.com", "password": PASSWORD})
    assert r.status_code == 401 and r.json()["error"]["code"] == "TOTP_REQUIRED"
    r = await client.post(f"{API}/auth/login", json={"email": "admin@example.com", "password": PASSWORD, "totp_code": pyotp.TOTP(secret).now()})
    assert r.status_code == 200


async def test_non_admin_cannot_use_admin_endpoints(client):
    await register_and_verify(client)
    assert (await client.get(f"{API}/admin/settings")).status_code == 403
    assert (await client.get(f"{API}/admin/audit")).status_code == 403


async def test_audit_log_is_append_only(client, db, make_admin):
    import pytest
    from sqlalchemy.exc import DBAPIError

    await make_admin()
    await client.post(f"{API}/auth/login", json={"email": "admin@example.com", "password": PASSWORD})
    entry = (await db.execute(select(AuditLog))).scalars().first()
    assert entry is not None and entry.action == "admin.login"
    for statement in ("UPDATE audit_logs SET action='x'", "DELETE FROM audit_logs"):
        with pytest.raises(DBAPIError, match="append-only"):
            await db.execute(text(statement))
        await db.rollback()
