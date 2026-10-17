import hashlib
import hmac
import json
import time

from sqlalchemy import select

from app.core.config import get_settings
from app.modules.audit.models import AuditLog
from app.modules.notifications.models import EmailOutbox
from tests.conftest import csrf_headers, register_and_verify
from tests.helpers import (
    API, COMPLETE_APPLICATION, PDF_BYTES, active_host, admin_client, approve_host, approved_host_without_stripe,
    png_bytes, submit_application,
)


async def test_admin_account_cannot_become_host(new_client, make_admin):
    admin = await admin_client(new_client, make_admin)
    r = await admin.post(f"{API}/host/apply", headers=csrf_headers(admin))
    assert r.status_code == 403 and r.json()["error"]["code"] == "ADMIN_CANNOT_HOST"


async def test_apply_requires_complete_form_id_document_and_host_terms(client):
    await register_and_verify(client, "h@example.com")
    h = csrf_headers(client)
    assert (await client.get(f"{API}/host")).json() == {"state": "none"}
    await client.post(f"{API}/host/apply", headers=h)

    r = await client.post(f"{API}/host/application/submit", json={"accept_host_terms": True}, headers=h)
    assert r.json()["error"]["code"] == "APPLICATION_INCOMPLETE"

    await client.patch(f"{API}/host/application", json=COMPLETE_APPLICATION, headers=h)
    r = await client.post(f"{API}/host/application/submit", json={"accept_host_terms": True}, headers=h)
    assert r.json()["error"]["code"] == "ID_REQUIRED"

    await client.post(f"{API}/host/documents", data={"doc_type": "government_id"},
                      files={"file": ("id.pdf", PDF_BYTES, "application/pdf")}, headers=h)
    r = await client.post(f"{API}/host/application/submit", json={"accept_host_terms": False}, headers=h)
    assert r.json()["error"]["code"] == "TERMS_REQUIRED"

    r = await client.post(f"{API}/host/application/submit", json={"accept_host_terms": True}, headers=h)
    assert r.status_code == 200 and r.json()["state"] == "pending_review"
    assert r.json()["editable"] is False
    # locked while under review
    r = await client.patch(f"{API}/host/application", json={"about": "changed"}, headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "UNDER_REVIEW"


async def test_business_hosts_need_a_business_name(client):
    await register_and_verify(client, "biz@example.com")
    h = csrf_headers(client)
    await client.post(f"{API}/host/apply", headers=h)
    await client.patch(f"{API}/host/application", json={**COMPLETE_APPLICATION, "host_type": "business"}, headers=h)
    await client.post(f"{API}/host/documents", data={"doc_type": "government_id"}, files={"file": ("a.png", png_bytes(), "image/png")}, headers=h)
    r = await client.post(f"{API}/host/application/submit", json={"accept_host_terms": True}, headers=h)
    assert r.json()["error"]["code"] == "APPLICATION_INCOMPLETE" and "business_name" in r.json()["error"]["message"]


async def test_document_upload_validation(client):
    await register_and_verify(client, "docs@example.com")
    h = csrf_headers(client)
    await client.post(f"{API}/host/apply", headers=h)
    bad = await client.post(f"{API}/host/documents", data={"doc_type": "government_id"},
                            files={"file": ("virus.png", b"MZ\x90\x00 not an image", "image/png")}, headers=h)
    assert bad.status_code == 415
    wrong_type = await client.post(f"{API}/host/documents", data={"doc_type": "passport_scan"},
                                   files={"file": ("a.png", png_bytes(), "image/png")}, headers=h)
    assert wrong_type.status_code == 422
    ok = await client.post(f"{API}/host/documents", data={"doc_type": "selfie"}, files={"file": ("a.png", png_bytes(), "image/png")}, headers=h)
    assert ok.status_code == 201
    doc = ok.json()["documents"][0]
    assert "storage_path" not in doc  # internal path is never exposed
    deleted = await client.delete(f"{API}/host/documents/{doc['id']}", headers=h)
    assert deleted.json()["documents"] == []


async def test_admin_review_flow_reject_changes_and_resubmit(new_client, make_admin):
    admin = await admin_client(new_client, make_admin)
    host = await new_client()
    data = await submit_application(host, "flow@example.com")
    ah = csrf_headers(admin)

    r = await admin.post(f"{API}/admin/hosts/{data['id']}/decision", json={"decision": "request_changes"}, headers=ah)
    assert r.json()["error"]["code"] == "NOTE_REQUIRED"
    r = await admin.post(f"{API}/admin/hosts/{data['id']}/decision", json={"decision": "request_changes", "note": "ID is blurry"}, headers=ah)
    assert r.json()["state"] == "changes_requested"

    mine = (await host.get(f"{API}/host")).json()
    assert mine["review_note"] == "ID is blurry" and mine["editable"] is True
    hh = csrf_headers(host)
    await host.post(f"{API}/host/documents", data={"doc_type": "government_id"}, files={"file": ("b.png", png_bytes("blue"), "image/png")}, headers=hh)
    r = await host.post(f"{API}/host/application/submit", json={"accept_host_terms": True}, headers=hh)
    assert r.json()["state"] == "pending_review"

    r = await admin.post(f"{API}/admin/hosts/{data['id']}/decision", json={"decision": "reject", "note": "Not eligible"}, headers=ah)
    assert r.json()["state"] == "rejected"
    # a decided application can't be decided again
    r = await admin.post(f"{API}/admin/hosts/{data['id']}/decision", json={"decision": "approve"}, headers=ah)
    assert r.status_code == 409 and r.json()["error"]["code"] == "NOT_PENDING"


async def test_host_becomes_active_only_with_approval_and_stripe(new_client, make_admin, db):
    admin = await admin_client(new_client, make_admin)
    host = await new_client()
    data = await submit_application(host, "gate@example.com")
    hh = csrf_headers(host)

    # not approved yet: no payout setup, no listings
    assert (await host.post(f"{API}/host/stripe/onboarding-link", headers=hh)).json()["error"]["code"] == "NOT_APPROVED"
    assert (await host.post(f"{API}/host/properties", json={"name": "X"}, headers=hh)).json()["error"]["code"] == "HOST_NOT_APPROVED"

    approved = await approve_host(admin, data["id"])
    assert approved["state"] == "approved"
    assert approved["can_create_listings"] is True and approved["can_submit_listings"] is False

    link = (await host.post(f"{API}/host/stripe/onboarding-link", headers=hh)).json()
    assert link["url"].startswith("http")
    status = (await host.get(f"{API}/host/stripe/status")).json()
    assert status["state"] == "approved" and status["stripe"]["ready"] is False and status["stripe"]["requirements"]

    done = (await host.post(f"{API}/host/stripe/dev-complete", headers=hh)).json()
    assert done["state"] == "active" and done["can_submit_listings"] is True
    assert [s["done"] for s in done["steps"]] == [True, True, True, True, True]
    mails = (await db.execute(select(EmailOutbox.template).where(EmailOutbox.to_email == "gate@example.com"))).scalars().all()
    assert {"host_submitted", "host_approved", "host_active"} <= set(mails)


async def test_suspend_and_reactivate_host_is_audited(new_client, make_admin, db):
    admin = await admin_client(new_client, make_admin)
    host, host_id = await active_host(new_client, admin)
    ah = csrf_headers(admin)
    r = await admin.post(f"{API}/admin/hosts/{host_id}/suspend", json={}, headers=ah)
    assert r.json()["error"]["code"] == "REASON_REQUIRED"
    r = await admin.post(f"{API}/admin/hosts/{host_id}/suspend", json={"reason": "Fraud report"}, headers=ah)
    assert r.json()["state"] == "suspended"
    mine = (await host.get(f"{API}/host")).json()
    assert mine["state"] == "suspended" and mine["can_create_listings"] is False and mine["suspended_reason"] == "Fraud report"
    r = await admin.post(f"{API}/admin/hosts/{host_id}/reactivate", headers=ah)
    assert r.json()["state"] == "active"
    actions = (await db.execute(select(AuditLog.action).where(AuditLog.entity_id == host_id))).scalars().all()
    assert {"host.suspended", "host.reactivated", "host.approve"} <= set(actions)


async def test_only_admins_see_documents_and_every_view_is_audited(new_client, make_admin, db):
    admin = await admin_client(new_client, make_admin)
    host = await new_client()
    data = await submit_application(host, "private@example.com")
    doc_id = data["documents"][0]["id"]
    url = f"{API}/admin/hosts/{data['id']}/documents/{doc_id}/file"

    other = await new_client()
    await register_and_verify(other, "snoop@example.com")
    assert (await other.get(url)).status_code == 403          # another user
    assert (await host.get(url)).status_code == 403           # even the owner uses the host endpoints, not this one
    anon = await new_client()
    assert (await anon.get(url)).status_code == 401

    r = await admin.get(url)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store" and r.headers["content-type"] == "image/png"
    logged = (await db.execute(select(AuditLog).where(AuditLog.action == "host_document.viewed"))).scalars().all()
    assert len(logged) == 1 and logged[0].actor_id is not None


async def test_private_files_are_not_served_from_the_public_media_route(new_client, make_admin):
    host = await new_client()
    data = await submit_application(host, "leak@example.com")
    r = await host.get(f"{API}/media/public/../private/host-docs/{data['id']}/x.png")
    assert r.status_code == 404
    r = await host.get(f"{API}/media/private/host-docs/{data['id']}/x.png")
    assert r.status_code in (404, 405)


async def test_per_host_overrides_are_validated_resolved_and_audited(new_client, make_admin, db):
    from app.modules.hosts import service as host_service
    from app.modules.settings import service as settings_service

    admin = await admin_client(new_client, make_admin)
    host, host_id = await active_host(new_client, admin)
    ah = csrf_headers(admin)

    bad = await admin.put(f"{API}/admin/hosts/{host_id}/overrides/checkin.interval_minutes", json={"value": 30}, headers=ah)
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "NOT_OVERRIDABLE"
    out_of_range = await admin.put(f"{API}/admin/hosts/{host_id}/overrides/finance.commission_percent", json={"value": 500}, headers=ah)
    assert out_of_range.status_code == 422

    ok = await admin.put(f"{API}/admin/hosts/{host_id}/overrides/finance.commission_percent", json={"value": 8.5, "reason": "Big hotel deal"}, headers=ah)
    assert ok.status_code == 200

    import uuid

    overrides = await host_service.get_overrides(db, uuid.UUID(host_id))
    effective = await settings_service.get_effective(db, "finance.commission_percent", overrides=overrides)
    assert (effective.value, effective.source) == (8.5, "override")
    assert (await settings_service.get_effective(db, "finance.commission_percent")).value == 12.0  # others unaffected

    detail = (await admin.get(f"{API}/admin/hosts/{host_id}")).json()
    commission = next(o for o in detail["overrides"] if o["key"] == "finance.commission_percent")
    assert commission["overridden"] is True and commission["value"] == 8.5 and commission["global_value"] == 12.0

    cleared = await admin.delete(f"{API}/admin/hosts/{host_id}/overrides/finance.commission_percent", headers=ah)
    assert cleared.status_code == 200
    actions = (await db.execute(select(AuditLog.action).where(AuditLog.entity_id == host_id))).scalars().all()
    assert {"host.override_set", "host.override_cleared"} <= set(actions)


async def test_admin_host_list_filters_by_state(new_client, make_admin):
    admin = await admin_client(new_client, make_admin)
    await active_host(new_client, admin, "a1@example.com")
    pending = await new_client()
    await submit_application(pending, "p1@example.com")
    pending_list = (await admin.get(f"{API}/admin/hosts", params={"state": "pending_review"})).json()
    assert [i["email"] for i in pending_list["items"]] == ["p1@example.com"]
    active_list = (await admin.get(f"{API}/admin/hosts", params={"state": "active"})).json()
    assert [i["email"] for i in active_list["items"]] == ["a1@example.com"]
    assert (await admin.get(f"{API}/admin/hosts", params={"q": "p1@"})).json()["total"] == 1


def _signed(secret: str, payload: dict) -> tuple[bytes, dict]:
    body = json.dumps(payload).encode()
    ts = str(int(time.time()))
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return body, {"stripe-signature": f"t={ts},v1={sig}", "content-type": "application/json"}


async def test_stripe_webhook_activates_host_once_and_rejects_bad_signatures(new_client, make_admin, db):
    settings = get_settings()
    settings.stripe_webhook_secret = "whsec_test_secret"
    try:
        admin = await admin_client(new_client, make_admin)
        host, host_id = await approved_host_without_stripe(new_client, admin, "wh@example.com")
        hh = csrf_headers(host)
        await host.post(f"{API}/host/stripe/onboarding-link", headers=hh)
        account_id = (await admin.get(f"{API}/admin/hosts/{host_id}")).json()["stripe"]["account_id"]

        event = {"id": "evt_1", "object": "event", "type": "account.updated", "data": {"object": {
            "id": account_id, "object": "account", "details_submitted": True, "payouts_enabled": True,
            "capabilities": {"transfers": "active"}, "requirements": {"currently_due": []}}}}
        anon = await new_client()

        body, headers = _signed("wrong_secret", event)
        assert (await anon.post(f"{API}/webhooks/stripe", content=body, headers=headers)).status_code == 400
        assert (await host.get(f"{API}/host")).json()["state"] == "approved"

        body, headers = _signed("whsec_test_secret", event)
        assert (await anon.post(f"{API}/webhooks/stripe", content=body, headers=headers)).json() == {"received": True}
        assert (await host.get(f"{API}/host")).json()["state"] == "active"

        again = await anon.post(f"{API}/webhooks/stripe", content=body, headers=headers)
        assert again.json().get("duplicate") is True
        count = (await db.execute(select(EmailOutbox).where(EmailOutbox.template == "host_active", EmailOutbox.to_email == "wh@example.com"))).scalars().all()
        assert len(count) == 1
    finally:
        settings.stripe_webhook_secret = ""


async def test_stripe_losing_payout_ability_restricts_the_host(new_client, make_admin, db):
    from app.modules.hosts.models import HostProfile
    from app.modules.hosts.service import host_state

    admin = await admin_client(new_client, make_admin)
    host, host_id = await active_host(new_client, admin, "restrict@example.com")
    import uuid

    profile = await db.get(HostProfile, uuid.UUID(host_id))
    profile.stripe_payouts_enabled = False
    await db.commit()
    assert host_state(profile) == "restricted"
    mine = (await host.get(f"{API}/host")).json()
    assert mine["state"] == "restricted" and mine["can_submit_listings"] is False


async def test_webhook_accepts_any_configured_signing_secret(new_client, make_admin):
    settings = get_settings()
    settings.stripe_webhook_secret = "whsec_platform, whsec_connect"
    try:
        admin = await admin_client(new_client, make_admin)
        host, host_id = await approved_host_without_stripe(new_client, admin, "two@example.com")
        await host.post(f"{API}/host/stripe/onboarding-link", headers=csrf_headers(host))
        account_id = (await admin.get(f"{API}/admin/hosts/{host_id}")).json()["stripe"]["account_id"]
        event = {"id": "evt_two", "object": "event", "type": "account.updated", "data": {"object": {
            "id": account_id, "details_submitted": True, "payouts_enabled": True,
            "capabilities": {"transfers": "active"}, "requirements": {"currently_due": []}}}}
        anon = await new_client()
        body, headers = _signed("whsec_connect", event)  # signed with the SECOND secret
        assert (await anon.post(f"{API}/webhooks/stripe", content=body, headers=headers)).status_code == 200
        assert (await host.get(f"{API}/host")).json()["state"] == "active"
        body, headers = _signed("whsec_other_project", {**event, "id": "evt_three"})
        assert (await anon.post(f"{API}/webhooks/stripe", content=body, headers=headers)).status_code == 400
    finally:
        settings.stripe_webhook_secret = ""


async def test_unsupported_host_country_is_refused_early_and_the_list_is_an_admin_setting(new_client, make_admin):
    admin = await admin_client(new_client, make_admin)
    host = await new_client()
    await register_and_verify(host, "india@example.com")
    h = csrf_headers(host)
    await host.post(f"{API}/host/apply", headers=h)
    r = await host.patch(f"{API}/host/application", json={"country": "in"}, headers=h)
    assert r.status_code == 422 and r.json()["error"]["code"] == "UNSUPPORTED_COUNTRY"
    assert "AU" in r.json()["error"]["message"]

    await admin.put(f"{API}/admin/settings/host.supported_countries", json={"value": ["AU", "IN"]}, headers=csrf_headers(admin))
    assert (await host.patch(f"{API}/host/application", json={"country": "IN"}, headers=h)).status_code == 200


async def test_payout_setup_is_blocked_for_a_host_whose_country_is_not_supported(new_client, make_admin, db):
    import uuid

    from app.modules.hosts.models import HostProfile

    admin = await admin_client(new_client, make_admin)
    host, host_id = await approved_host_without_stripe(new_client, admin, "legacy@example.com")
    profile = await db.get(HostProfile, uuid.UUID(host_id))
    profile.application_data = {**profile.application_data, "country": "IN"}  # approved before the rule existed
    await db.commit()
    r = await host.post(f"{API}/host/stripe/onboarding-link", headers=csrf_headers(host))
    assert r.status_code == 422 and r.json()["error"]["code"] == "UNSUPPORTED_COUNTRY"


def test_stripe_errors_become_readable_messages_not_a_crash():
    import stripe

    from app.core.errors import AppError
    from app.modules.payments.stripe_gateway import RealStripeGateway

    class Boom:
        def create(self, **_):
            raise stripe.InvalidRequestError("Connected accounts in IN cannot be created by platforms in AU.", param=None)

    class Client:
        v2 = type("V2", (), {"core": type("Core", (), {"accounts": Boom()})()})()

    gateway = RealStripeGateway.__new__(RealStripeGateway)
    gateway._client = Client()
    try:
        gateway.create_account(email="a@example.com", country="IN", host_id="h1")
        raise AssertionError("expected an error")
    except AppError as exc:
        assert exc.status_code == 502 and exc.code == "STRIPE_ERROR"
        assert "payout setup" in exc.message


async def test_webhook_ignores_events_for_accounts_that_are_not_ours(new_client):
    """The same Stripe account can serve several projects; events about other projects' accounts must be harmless."""
    settings = get_settings()
    settings.stripe_webhook_secret = "whsec_shared"
    try:
        event = {"id": "evt_foreign", "object": "event", "type": "account.updated", "data": {"object": {
            "id": "acct_belongs_to_another_project", "details_submitted": True, "payouts_enabled": True,
            "capabilities": {"transfers": "active"}}}}
        anon = await new_client()
        body, headers = _signed("whsec_shared", event)
        assert (await anon.post(f"{API}/webhooks/stripe", content=body, headers=headers)).status_code == 200
        unrelated = {"id": "evt_other_type", "object": "event", "type": "customer.created", "data": {"object": {"id": "cus_1"}}}
        body, headers = _signed("whsec_shared", unrelated)
        assert (await anon.post(f"{API}/webhooks/stripe", content=body, headers=headers)).status_code == 200
    finally:
        settings.stripe_webhook_secret = ""
