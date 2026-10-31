"""Push notifications to the mobile app through Firebase Cloud Messaging (FCM HTTP v1).

Every in-app notification is also pushed to the person's phones. The bell row is the source of truth: it is written in the
same transaction as the event, and this sender picks up rows with no `pushed_at`, like the email outbox. One try per row
(a missed push is not worth retrying: the notification is still in the app). Old rows are never pushed.

Firebase's service-account key stays on the server (FIREBASE_SERVICE_ACCOUNT_B64). With no key, rows are marked as handled
and nothing is sent.

NOTE: written from Firebase's documentation and tested with a simulated Firebase only. Check with a real phone before relying on it."""
import base64
import json
import logging
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError
from app.modules.notifications.models import DeviceToken, Notification

log = logging.getLogger(__name__)
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
MAX_AGE = timedelta(hours=6)       # a push about something older than this is noise, not news
PLATFORMS = ("android", "ios")
_cached: dict[str, Any] = {}       # Google access token, reused until shortly before it expires


def get_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=10)


def credentials() -> dict | None:
    raw = get_settings().firebase_service_account_b64
    if not raw:
        return None
    try:
        return json.loads(base64.b64decode(raw))
    except Exception:  # noqa: BLE001
        log.error("FIREBASE_SERVICE_ACCOUNT_B64 is not valid base64 JSON; pushes are off")
        return None


def enabled() -> bool:
    return credentials() is not None


# ---------- phones ----------

async def register_device(session: AsyncSession, user_id: uuid.UUID, token: str, platform: str) -> None:
    """Saves (or moves) a phone's token. A token belongs to one install: if someone else signed in on that phone before, it is theirs no more."""
    if platform not in PLATFORMS:
        raise AppError(422, "INVALID_PLATFORM", "Platform must be android or ios.")
    row = (await session.execute(select(DeviceToken).where(DeviceToken.token == token))).scalar_one_or_none()
    if row is None:
        session.add(DeviceToken(user_id=user_id, token=token, platform=platform))
    else:
        row.user_id, row.platform, row.last_seen_at = user_id, platform, datetime.now(UTC)


async def unregister_device(session: AsyncSession, user_id: uuid.UUID, token: str) -> None:
    await session.execute(delete(DeviceToken).where(DeviceToken.token == token, DeviceToken.user_id == user_id))


# ---------- sending ----------

async def _access_token(client: httpx.AsyncClient, creds: dict) -> str:
    if _cached.get("token") and _cached.get("expires", 0) > time.time() + 60:
        return _cached["token"]
    now = int(time.time())
    assertion = jwt.encode({"iss": creds["client_email"], "scope": SCOPE, "aud": TOKEN_URL, "iat": now, "exp": now + 3600},
                           creds["private_key"], algorithm="RS256")
    resp = await client.post(TOKEN_URL, data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion})
    resp.raise_for_status()
    data = resp.json()
    _cached.update(token=data["access_token"], expires=now + int(data.get("expires_in", 3600)))
    return _cached["token"]


def message(n: Notification, token: str) -> dict:
    """What Firebase shows on the phone. `data` lets the app open the right screen when the push is tapped."""
    return {"message": {
        "token": token,
        "notification": {"title": n.title, "body": n.body or "Tap to see the details."},
        "data": {"notification_id": str(n.id), "kind": n.kind, "link": n.link},
        "android": {"priority": "high", "notification": {"channel_id": "updates"}},
    }}


def _token_is_dead(resp: httpx.Response) -> bool:
    """The app was uninstalled or the token replaced: Firebase says so with 404 UNREGISTERED (or 400 on a malformed token)."""
    if resp.status_code == 404:
        return True
    try:
        details = resp.json().get("error", {}).get("details", [])
    except Exception:  # noqa: BLE001
        return False
    return any(d.get("errorCode") in ("UNREGISTERED", "INVALID_ARGUMENT") for d in details)


async def process_pushes(session: AsyncSession, limit: int = 100) -> int:
    """Pushes new notifications to the phones of the people they are for. Returns how many messages Firebase accepted."""
    now = datetime.now(UTC)
    rows = (await session.execute(
        select(Notification).where(Notification.pushed_at.is_(None)).order_by(Notification.created_at).limit(limit).with_for_update(skip_locked=True)
    )).scalars().all()
    if not rows:
        return 0
    creds = credentials()
    fresh = [n for n in rows if n.created_at >= now - MAX_AGE]
    tokens: dict[uuid.UUID, list[DeviceToken]] = {}
    if creds and fresh:
        for d in (await session.execute(select(DeviceToken).where(DeviceToken.user_id.in_({n.user_id for n in fresh})))).scalars():
            tokens.setdefault(d.user_id, []).append(d)
    sent = 0
    if tokens:
        try:
            async with get_http_client() as client:
                access = await _access_token(client, creds)
                url = f"https://fcm.googleapis.com/v1/projects/{creds['project_id']}/messages:send"
                for n in fresh:
                    for device in tokens.get(n.user_id, []):
                        resp = await client.post(url, json=message(n, device.token), headers={"Authorization": f"Bearer {access}"})
                        if resp.status_code == 200:
                            sent += 1
                        elif _token_is_dead(resp):
                            await session.delete(device)
                            tokens[n.user_id] = [d for d in tokens[n.user_id] if d is not device]
                        else:
                            log.warning("push failed notification=%s status=%s", n.id, resp.status_code)
        except Exception:  # noqa: BLE001  Firebase down or the key revoked: the bell still has everything
            log.exception("push sending failed")
    for n in rows:
        n.pushed_at = now
    await session.commit()
    return sent
