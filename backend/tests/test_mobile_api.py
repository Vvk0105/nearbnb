"""What the mobile app needs from the API: token sign-in, no captcha, account deletion and push notifications.
The website's cookie sign-in must not change."""
import base64
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import select, update

from app.core.config import get_settings
from app.modules.bookings.models import Booking
from app.modules.notifications import push
from app.modules.notifications.models import DeviceToken, Notification
from app.modules.users.models import User
from tests.conftest import PASSWORD, csrf_headers, register_and_verify
from tests.helpers import API
from tests.test_bookings import book, complete_guest, emails, world

MOBILE = {"X-Client": "mobile"}
TOKEN_A = "fcm-token-aaaaaaaaaaaaaaaaaaaaaaaa"
TOKEN_B = "fcm-token-bbbbbbbbbbbbbbbbbbbbbbbb"


async def mobile_login(client, email="guest@example.com", password=PASSWORD):
    r = await client.post(f"{API}/auth/login", json={"email": email, "password": password}, headers=MOBILE)
    assert r.status_code == 200, r.text
    return r.json()


def bearer(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def signed_up_on_phone(new_client, email="guest@example.com") -> tuple[httpx.AsyncClient, dict]:
    web = await new_client()
    await register_and_verify(web, email)
    phone = await new_client()
    return phone, (await mobile_login(phone, email))["tokens"]


# ---------- signing in ----------

async def test_the_app_gets_tokens_in_the_body_and_no_cookies(new_client):
    phone, tokens = await signed_up_on_phone(new_client)
    assert tokens["token_type"] == "bearer" and tokens["access_token"] and tokens["refresh_token"] and tokens["expires_in"] > 0
    assert phone.cookies.get("access_token") is None and phone.cookies.get("csrf_token") is None
    me = await phone.get(f"{API}/me", headers=bearer(tokens))
    assert me.status_code == 200 and me.json()["email"] == "guest@example.com"


async def test_the_website_sign_in_is_unchanged(client):
    await register_and_verify(client)
    client.cookies.clear()
    r = await client.post(f"{API}/auth/login", json={"email": "guest@example.com", "password": PASSWORD})
    assert r.status_code == 200 and "tokens" not in r.json()
    assert client.cookies.get("access_token") and client.cookies.get("csrf_token")


async def test_verifying_the_email_in_the_app_signs_it_in_with_tokens(client):
    r = await client.post(f"{API}/auth/register", json={"email": "new@example.com", "password": PASSWORD, "full_name": "New", "accept_terms": True}, headers=MOBILE)
    assert r.status_code == 202
    from tests.conftest import latest_code

    r = await client.post(f"{API}/auth/verify-email", json={"email": "new@example.com", "code": await latest_code("new@example.com")}, headers=MOBILE)
    assert r.status_code == 200 and r.json()["tokens"]["access_token"]
    assert client.cookies.get("access_token") is None


async def test_the_app_can_change_things_without_a_csrf_token(new_client):
    phone, tokens = await signed_up_on_phone(new_client)
    r = await phone.patch(f"{API}/me", json={"full_name": "Phone Person"}, headers=bearer(tokens))
    assert r.status_code == 200 and r.json()["full_name"] == "Phone Person"


async def test_a_wrong_or_missing_token_is_refused(client):
    assert (await client.get(f"{API}/me", headers={"Authorization": "Bearer not-a-real-token"})).status_code == 401
    assert (await client.get(f"{API}/me", headers={"Authorization": "Basic abc"})).status_code == 401
    assert (await client.get(f"{API}/me")).status_code == 401


async def test_the_app_skips_the_captcha_but_the_website_does_not(new_client):
    web = await new_client()
    await register_and_verify(web)
    settings = get_settings()
    settings.turnstile_secret_key = "secret-for-test"
    try:
        r = await (await new_client()).post(f"{API}/auth/login", json={"email": "guest@example.com", "password": PASSWORD})
        assert r.status_code == 400 and r.json()["error"]["code"] == "CAPTCHA_REQUIRED"
        await mobile_login(await new_client())
    finally:
        settings.turnstile_secret_key = ""


async def test_refresh_gives_new_tokens_and_a_used_refresh_token_is_dead(new_client):
    phone, tokens = await signed_up_on_phone(new_client)
    r = await phone.post(f"{API}/auth/refresh", headers={**MOBILE, "X-Refresh-Token": tokens["refresh_token"]})
    assert r.status_code == 200
    fresh = r.json()["tokens"]
    assert fresh["refresh_token"] != tokens["refresh_token"]
    assert (await phone.get(f"{API}/me", headers=bearer(fresh))).status_code == 200
    replay = await phone.post(f"{API}/auth/refresh", headers={**MOBILE, "X-Refresh-Token": tokens["refresh_token"]})
    assert replay.status_code == 401   # a stolen, already-used token kills the whole sign-in
    assert (await phone.post(f"{API}/auth/refresh", headers={**MOBILE, "X-Refresh-Token": fresh["refresh_token"]})).status_code == 401


async def test_signing_out_in_the_app_ends_that_sign_in(new_client):
    phone, tokens = await signed_up_on_phone(new_client)
    assert (await phone.post(f"{API}/auth/logout", headers={**MOBILE, "X-Refresh-Token": tokens["refresh_token"]})).status_code == 200
    assert (await phone.post(f"{API}/auth/refresh", headers={**MOBILE, "X-Refresh-Token": tokens["refresh_token"]})).status_code == 401


async def test_changing_the_password_in_the_app_keeps_the_app_signed_in(new_client):
    phone, tokens = await signed_up_on_phone(new_client)
    headers = {**bearer(tokens), "X-Refresh-Token": tokens["refresh_token"]}
    r = await phone.post(f"{API}/auth/change-password", json={"current_password": PASSWORD, "new_password": "An0ther-long-pass"}, headers=headers)
    assert r.status_code == 200
    assert (await phone.post(f"{API}/auth/refresh", headers={**MOBILE, "X-Refresh-Token": tokens["refresh_token"]})).status_code == 200


# ---------- deleting the account ----------

async def test_deleting_the_account_erases_the_details_and_frees_the_email(new_client, db):
    phone, tokens = await signed_up_on_phone(new_client)
    await phone.patch(f"{API}/me", json={"phone": "+61 400 111 222", "dob": "1990-01-01"}, headers=bearer(tokens))
    await phone.post(f"{API}/me/notifications/devices", json={"token": TOKEN_A, "platform": "android"}, headers=bearer(tokens))

    assert (await phone.post(f"{API}/me/delete", json={"password": "wrong"}, headers=bearer(tokens))).status_code == 400
    r = await phone.post(f"{API}/me/delete", json={"password": PASSWORD}, headers=bearer(tokens))
    assert r.status_code == 200

    user = (await db.execute(select(User).where(User.full_name == "Deleted user"))).scalar_one()
    assert user.email.endswith("@deleted.invalid") and user.phone is None and user.dob is None and user.status == "deactivated"
    assert (await db.execute(select(DeviceToken))).scalars().all() == []
    assert len(await emails(db, "account_deleted", "guest@example.com")) == 1     # a goodbye to the real address
    assert (await phone.get(f"{API}/me", headers=bearer(tokens))).status_code == 401
    assert (await phone.post(f"{API}/auth/refresh", headers={**MOBILE, "X-Refresh-Token": tokens["refresh_token"]})).status_code == 401
    assert (await phone.post(f"{API}/auth/login", json={"email": "guest@example.com", "password": PASSWORD}, headers=MOBILE)).status_code == 401
    again = await new_client()
    await register_and_verify(again)   # the same email can sign up again as a new person


async def test_the_website_can_delete_the_account_too(client):
    await register_and_verify(client)
    r = await client.post(f"{API}/me/delete", json={"password": PASSWORD}, headers=csrf_headers(client))
    assert r.status_code == 200
    assert client.cookies.get("access_token") is None


async def test_a_booking_in_progress_blocks_deletion_and_a_finished_one_loses_the_guests_details(new_client, make_admin, db):
    _, host, _, _, unit = await world(new_client, make_admin)
    guest = await complete_guest(new_client)
    booking = (await book(guest, unit)).json()
    r = await guest.post(f"{API}/me/delete", json={"password": PASSWORD}, headers=csrf_headers(guest))
    assert r.status_code == 409 and r.json()["error"]["code"] == "OPEN_BOOKINGS"
    assert (await db.execute(select(User).where(User.email == "guest@example.com"))).scalar_one().full_name != "Deleted user"

    assert (await guest.post(f"{API}/me/bookings/{booking['id']}/withdraw", headers=csrf_headers(guest))).status_code == 200
    assert (await guest.post(f"{API}/me/delete", json={"password": PASSWORD}, headers=csrf_headers(guest))).status_code == 200
    db.expire_all()
    kept = (await db.execute(select(Booking))).scalar_one()
    assert kept.guest_snapshot == {"full_name": "Deleted user", "deleted": True}      # the booking stays, the person does not


async def test_hosts_and_admins_cannot_delete_themselves_here(new_client, make_admin):
    _, host, _, _, _ = await world(new_client, make_admin)
    r = await host.post(f"{API}/me/delete", json={"password": PASSWORD}, headers=csrf_headers(host))
    assert r.status_code == 409 and r.json()["error"]["code"] == "HOST_ACCOUNT"
    admin = await new_client()
    await admin.post(f"{API}/auth/login", json={"email": "admin@example.com", "password": PASSWORD})
    assert (await admin.post(f"{API}/me/delete", json={"password": PASSWORD}, headers=csrf_headers(admin))).status_code == 403


# ---------- phones and pushes ----------

async def test_a_phone_token_is_saved_moved_and_removed(new_client, db):
    phone, tokens = await signed_up_on_phone(new_client)
    h = bearer(tokens)
    assert (await phone.post(f"{API}/me/notifications/devices", json={"token": TOKEN_A, "platform": "android"}, headers=h)).status_code == 200
    assert (await phone.post(f"{API}/me/notifications/devices", json={"token": TOKEN_A, "platform": "android"}, headers=h)).status_code == 200
    assert (await phone.post(f"{API}/me/notifications/devices", json={"token": TOKEN_A, "platform": "windows"}, headers=h)).status_code == 422
    rows = (await db.execute(select(DeviceToken))).scalars().all()
    assert len(rows) == 1 and rows[0].platform == "android"

    other_phone, other = await signed_up_on_phone(new_client, "second@example.com")   # someone else signs in on the same phone
    await other_phone.post(f"{API}/me/notifications/devices", json={"token": TOKEN_A, "platform": "android"}, headers=bearer(other))
    db.expire_all()
    owner = (await db.execute(select(DeviceToken))).scalar_one().user_id
    assert owner == (await db.execute(select(User.id).where(User.email == "second@example.com"))).scalar_one()

    await phone.post(f"{API}/me/notifications/devices/remove", json={"token": TOKEN_A}, headers=h)      # not theirs any more: no effect
    assert len((await db.execute(select(DeviceToken))).scalars().all()) == 1
    await other_phone.post(f"{API}/me/notifications/devices/remove", json={"token": TOKEN_A}, headers=bearer(other))
    db.expire_all()
    assert (await db.execute(select(DeviceToken))).scalars().all() == []


def _fake_service_account() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    info = {"type": "service_account", "project_id": "nearbnb-test", "client_email": "push@nearbnb-test.iam.gserviceaccount.com", "private_key": pem}
    return base64.b64encode(json.dumps(info).encode()).decode()


@pytest.fixture
def firebase(monkeypatch):
    """Pretends to be Google and Firebase. TOKEN_B is a phone that uninstalled the app."""
    settings = get_settings()
    settings.firebase_service_account_b64 = _fake_service_account()
    push._cached.clear()
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "google-access", "expires_in": 3600})
        if TOKEN_B in request.content.decode():
            return httpx.Response(404, json={"error": {"status": "NOT_FOUND", "details": [{"errorCode": "UNREGISTERED"}]}})
        return httpx.Response(200, json={"name": "projects/nearbnb-test/messages/1"})

    monkeypatch.setattr(push, "get_http_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    yield seen
    settings.firebase_service_account_b64 = ""
    push._cached.clear()


async def _guest_with_phones(new_client, make_admin, db, *tokens):
    _, host, _, _, unit = await world(new_client, make_admin)
    guest = await complete_guest(new_client)
    for t in tokens:
        await guest.post(f"{API}/me/notifications/devices", json={"token": t, "platform": "android"}, headers=csrf_headers(guest))
    await db.execute(update(Notification).values(pushed_at=datetime.now(UTC)))   # only what happens from here on
    await db.commit()
    booking = (await book(guest, unit)).json()
    return guest, booking


async def test_a_new_notification_is_pushed_to_the_persons_phone_and_a_dead_phone_is_forgotten(new_client, make_admin, db, firebase):
    _, booking = await _guest_with_phones(new_client, make_admin, db, TOKEN_A, TOKEN_B)
    assert await push.process_pushes(db) == 1

    sends = [r for r in firebase if r.url.host == "fcm.googleapis.com"]
    assert all(r.url.path == "/v1/projects/nearbnb-test/messages:send" and r.headers["authorization"] == "Bearer google-access" for r in sends)
    to_a = [json.loads(r.content)["message"] for r in sends if TOKEN_A in r.content.decode()]
    assert len(to_a) == 1 and to_a[0]["data"]["link"] == f"/account/trips/{booking['id']}" and to_a[0]["data"]["kind"] == "booking_request_guest"
    assert to_a[0]["notification"]["title"]
    assert [d.token for d in (await db.execute(select(DeviceToken))).scalars()] == [TOKEN_A]
    assert all(n.pushed_at for n in (await db.execute(select(Notification))).scalars())

    before = len(firebase)
    assert await push.process_pushes(db) == 0 and len(firebase) == before     # never twice


async def test_old_notifications_and_people_without_phones_send_nothing(new_client, make_admin, db, firebase):
    await _guest_with_phones(new_client, make_admin, db, TOKEN_A)
    await db.execute(update(Notification).where(Notification.pushed_at.is_(None)).values(created_at=datetime.now(UTC) - timedelta(days=1)))
    await db.commit()
    assert await push.process_pushes(db) == 0
    assert [r for r in firebase if r.url.host == "fcm.googleapis.com"] == []
    assert all(n.pushed_at for n in (await db.execute(select(Notification))).scalars())


async def test_without_a_firebase_key_nothing_is_sent_and_nothing_piles_up(new_client, make_admin, db):
    await _guest_with_phones(new_client, make_admin, db, TOKEN_A)
    assert await push.process_pushes(db) == 0
    assert all(n.pushed_at for n in (await db.execute(select(Notification))).scalars())


async def test_firebase_being_down_does_not_break_anything(new_client, make_admin, db, monkeypatch):
    get_settings().firebase_service_account_b64 = _fake_service_account()
    push._cached.clear()
    monkeypatch.setattr(push, "get_http_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503))))
    try:
        await _guest_with_phones(new_client, make_admin, db, TOKEN_A)
        assert await push.process_pushes(db) == 0
        assert all(n.pushed_at for n in (await db.execute(select(Notification))).scalars())
        assert len((await db.execute(select(DeviceToken))).scalars().all()) == 1
    finally:
        get_settings().firebase_service_account_b64 = ""
