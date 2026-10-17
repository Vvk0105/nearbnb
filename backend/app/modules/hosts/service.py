import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.storage import get_storage
from app.modules.audit import service as audit
from app.modules.hosts.models import (
    DOC_TYPES, REVIEW_APPROVED, REVIEW_CHANGES, REVIEW_DRAFT, REVIEW_PENDING, REVIEW_REJECTED,
    HostDocument, HostProfile, HostSettingOverride,
)
from app.modules.legal import service as legal
from app.modules.notifications.service import enqueue_email
from app.modules.payments.stripe_gateway import AccountStatus, get_gateway
from app.modules.settings import service as settings_service
from app.modules.settings.registry import get_def
from app.modules.users.models import User
from app.modules.users.service import validate_phone

EDITABLE_REVIEW = (REVIEW_DRAFT, REVIEW_CHANGES, REVIEW_REJECTED)
MAX_DOCUMENTS = 6
MAX_DOC_BYTES = 10 * 1024 * 1024
STATE_SUSPENDED = "suspended"
STATE_ACTIVE = "active"
STATE_APPROVED = "approved"      # approved by admin, payout setup still missing
STATE_RESTRICTED = "restricted"  # was active, Stripe now blocks payouts


class ApplicationDraft(BaseModel):
    host_type: Literal["individual", "business"] | None = None
    legal_name: str | None = Field(default=None, max_length=200)
    business_name: str | None = Field(default=None, max_length=200)
    business_number: str | None = Field(default=None, max_length=50)
    phone: str | None = None
    address_line1: str | None = Field(default=None, max_length=200)
    suburb: str | None = Field(default=None, max_length=100)
    state: str | None = Field(default=None, max_length=50)
    postcode: str | None = Field(default=None, max_length=20)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    about: str | None = Field(default=None, max_length=2000)


class ApplicationComplete(BaseModel):
    host_type: Literal["individual", "business"]
    legal_name: str = Field(min_length=1, max_length=200)
    business_name: str | None = None
    business_number: str | None = None
    phone: str
    address_line1: str = Field(min_length=1, max_length=200)
    suburb: str = Field(min_length=1, max_length=100)
    state: str = Field(min_length=1, max_length=50)
    postcode: str = Field(min_length=1, max_length=20)
    country: str = Field(min_length=2, max_length=2)
    about: str = Field(min_length=1, max_length=2000)


# ---------- state ----------

def stripe_ready(host: HostProfile) -> bool:
    return host.stripe_details_submitted and host.stripe_payouts_enabled and host.stripe_transfers_active


def host_state(host: HostProfile) -> str:
    if host.suspended_at:
        return STATE_SUSPENDED
    if host.review_status != REVIEW_APPROVED:
        return host.review_status
    if stripe_ready(host):
        return STATE_ACTIVE
    return STATE_RESTRICTED if host.activated_at else STATE_APPROVED


def is_active(host: HostProfile) -> bool:
    return host_state(host) == STATE_ACTIVE


async def get_host(session: AsyncSession, user: User) -> HostProfile | None:
    return (await session.execute(select(HostProfile).where(HostProfile.user_id == user.id))).scalar_one_or_none()


async def require_host(session: AsyncSession, user: User) -> HostProfile:
    host = await get_host(session, user)
    if host is None:
        raise AppError(404, "NOT_A_HOST", "You have not applied to become a host.")
    return host


async def documents_of(session: AsyncSession, host_id: uuid.UUID) -> list[HostDocument]:
    return list(
        (await session.execute(select(HostDocument).where(HostDocument.host_id == host_id).order_by(HostDocument.created_at))).scalars()
    )


async def serialize_host(session: AsyncSession, host: HostProfile, *, for_admin: bool = False) -> dict:
    docs = await documents_of(session, host.id)
    state = host_state(host)
    settings = get_settings()
    steps = [
        {"key": "application", "label": "Application", "done": host.review_status in (REVIEW_PENDING, REVIEW_APPROVED)},
        {"key": "documents", "label": "ID documents", "done": any(d.doc_type == "government_id" for d in docs)},
        {"key": "review", "label": "Admin review", "done": host.review_status == REVIEW_APPROVED},
        {"key": "payouts", "label": "Payout setup", "done": stripe_ready(host)},
        {"key": "active", "label": "Active", "done": state == STATE_ACTIVE},
    ]
    return {
        "id": str(host.id),
        "state": state,
        "review_status": host.review_status,
        "review_note": host.review_note,
        "suspended_reason": host.suspended_reason if host.suspended_at else None,
        "application": host.application_data or {},
        "editable": host.review_status in EDITABLE_REVIEW and not host.suspended_at,
        "documents": [
            {"id": str(d.id), "doc_type": d.doc_type, "original_name": d.original_name, "size_bytes": d.size_bytes,
             "content_type": d.content_type, "created_at": d.created_at.isoformat() if d.created_at else None}
            for d in docs
        ],
        "steps": steps,
        "stripe": {
            "connected": host.stripe_account_id is not None,
            "details_submitted": host.stripe_details_submitted,
            "payouts_enabled": host.stripe_payouts_enabled,
            "transfers_active": host.stripe_transfers_active,
            "ready": stripe_ready(host),
            "requirements": host.stripe_requirements or [],
            "dev_mode": not settings.stripe_secret_key and not settings.is_production,
            **({"account_id": host.stripe_account_id} if for_admin else {}),
        },
        "can_create_listings": state in (STATE_APPROVED, STATE_ACTIVE, STATE_RESTRICTED) and not host.suspended_at,
        "can_submit_listings": state == STATE_ACTIVE,
        "submitted_at": host.submitted_at.isoformat() if host.submitted_at else None,
        "approved_at": host.approved_at.isoformat() if host.approved_at else None,
        "activated_at": host.activated_at.isoformat() if host.activated_at else None,
    }


# ---------- application ----------

async def apply(session: AsyncSession, user: User) -> HostProfile:
    if user.is_admin:
        raise AppError(403, "ADMIN_CANNOT_HOST", "Administrator accounts cannot become hosts. Use a separate account.")
    host = await get_host(session, user)
    if host is None:
        host = HostProfile(user_id=user.id, application_data={"legal_name": user.full_name, "country": "AU"})
        session.add(host)
        await session.flush()
    return host


def _assert_editable(host: HostProfile) -> None:
    if host.suspended_at:
        raise AppError(403, "HOST_SUSPENDED", "Your host account is suspended.")
    if host.review_status not in EDITABLE_REVIEW:
        raise AppError(409, "UNDER_REVIEW", "Your application can't be edited right now.")


async def _check_country(session: AsyncSession, country: str | None) -> None:
    supported = [c.upper() for c in await settings_service.get_value(session, "host.supported_countries")]
    if country and country.upper() not in supported:
        raise AppError(422, "UNSUPPORTED_COUNTRY",
                       f"NearBNB currently accepts hosts in: {', '.join(supported)}. '{country.upper()}' is not supported yet.")


async def save_draft(session: AsyncSession, host: HostProfile, body: ApplicationDraft) -> None:
    _assert_editable(host)
    data = dict(host.application_data or {})
    for key, value in body.model_dump(exclude_unset=True).items():
        if value is None or (isinstance(value, str) and not value.strip()):
            data.pop(key, None)
        else:
            data[key] = value.strip() if isinstance(value, str) else value
    if data.get("country"):
        data["country"] = data["country"].upper()
        await _check_country(session, data["country"])
    if data.get("phone"):
        data["phone"] = validate_phone(data["phone"])
    host.application_data = data  # reassign so the JSONB change is detected


def _missing_fields(data: dict) -> list[str]:
    try:
        ApplicationComplete.model_validate(data)
    except ValidationError as exc:
        return sorted({str(e["loc"][0]) for e in exc.errors() if e["loc"]})
    if data.get("host_type") == "business" and not data.get("business_name"):
        return ["business_name"]
    return []


def _detect_document(data: bytes) -> tuple[str, str]:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", ".webp"
    if data.startswith(b"%PDF"):
        return "application/pdf", ".pdf"
    raise AppError(415, "UNSUPPORTED_FILE", "Upload a JPEG, PNG, WebP or PDF file.")


async def add_document(session: AsyncSession, host: HostProfile, doc_type: str, filename: str, data: bytes) -> HostDocument:
    _assert_editable(host)
    if doc_type not in DOC_TYPES:
        raise AppError(422, "INVALID_DOCUMENT_TYPE", f"Document type must be one of: {', '.join(DOC_TYPES)}")
    if not data:
        raise AppError(422, "EMPTY_FILE", "The file is empty.")
    if len(data) > MAX_DOC_BYTES:
        raise AppError(413, "FILE_TOO_LARGE", "Files must be 10 MB or smaller.")
    if len(await documents_of(session, host.id)) >= MAX_DOCUMENTS:
        raise AppError(422, "TOO_MANY_DOCUMENTS", f"You can upload at most {MAX_DOCUMENTS} documents.")
    content_type, ext = _detect_document(data)
    path = f"private/host-docs/{host.id}/{uuid.uuid4().hex}{ext}"
    get_storage().save(path, data)
    doc = HostDocument(
        host_id=host.id, doc_type=doc_type, storage_path=path,
        original_name=(filename or "document")[:255], content_type=content_type, size_bytes=len(data),
    )
    session.add(doc)
    await session.flush()
    return doc


async def delete_document(session: AsyncSession, host: HostProfile, doc_id: uuid.UUID) -> None:
    _assert_editable(host)
    doc = await session.get(HostDocument, doc_id)
    if doc is None or doc.host_id != host.id:
        raise AppError(404, "NOT_FOUND")
    get_storage().delete(doc.storage_path)
    await session.delete(doc)


async def submit(session: AsyncSession, host: HostProfile, user: User, *, accept_host_terms: bool, ip: str | None) -> None:
    _assert_editable(host)
    if not accept_host_terms:
        raise AppError(422, "TERMS_REQUIRED", "You must accept the Host Terms.")
    await _check_country(session, (host.application_data or {}).get("country"))
    missing = _missing_fields(host.application_data or {})
    if missing:
        raise AppError(422, "APPLICATION_INCOMPLETE", "Please complete: " + ", ".join(missing))
    if not any(d.doc_type == "government_id" for d in await documents_of(session, host.id)):
        raise AppError(422, "ID_REQUIRED", "Upload a government-issued ID before submitting.")
    await legal.record_consent(session, user.id, "host_terms", ip)
    before = host.review_status
    host.review_status = REVIEW_PENDING
    host.submitted_at = datetime.now(UTC)
    audit.record(session, action="host.submitted", entity_type="host", entity_id=host.id, actor_id=user.id,
                 before={"review_status": before}, after={"review_status": REVIEW_PENDING}, ip=ip)
    enqueue_email(session, to_email=user.email, template="host_submitted", context={"name": user.full_name}, user_id=user.id)


# ---------- admin decisions ----------

DECISIONS = {"approve": REVIEW_APPROVED, "reject": REVIEW_REJECTED, "request_changes": REVIEW_CHANGES}


async def decide(session: AsyncSession, host: HostProfile, *, decision: str, note: str | None, admin: User, ip: str | None) -> None:
    if decision not in DECISIONS:
        raise AppError(422, "INVALID_DECISION", "Decision must be approve, reject or request_changes.")
    if host.review_status != REVIEW_PENDING:
        raise AppError(409, "NOT_PENDING", "This application is not waiting for review.")
    if decision != "approve" and not (note or "").strip():
        raise AppError(422, "NOTE_REQUIRED", "Give the host a reason.")
    before = host.review_status
    host.review_status = DECISIONS[decision]
    host.review_note = (note or "").strip() or None
    host.reviewed_at = datetime.now(UTC)
    host.reviewed_by = admin.id
    if decision == "approve":
        host.approved_at = datetime.now(UTC)
    audit.record(session, action=f"host.{decision}", entity_type="host", entity_id=host.id, actor_id=admin.id,
                 before={"review_status": before}, after={"review_status": host.review_status}, reason=host.review_note, ip=ip)
    owner = await session.get(User, host.user_id)
    template = {"approve": "host_approved", "reject": "host_rejected", "request_changes": "host_changes_requested"}[decision]
    enqueue_email(session, to_email=owner.email, template=template,
                  context={"name": owner.full_name, "note": host.review_note or ""}, user_id=owner.id)


async def set_suspended(session: AsyncSession, host: HostProfile, *, suspend: bool, reason: str | None, admin: User, ip: str | None) -> None:
    if suspend:
        if not (reason or "").strip():
            raise AppError(422, "REASON_REQUIRED", "Give a reason for the suspension.")
        if host.suspended_at:
            raise AppError(409, "ALREADY_SUSPENDED", "This host is already suspended.")
        host.suspended_at = datetime.now(UTC)
        host.suspended_reason = reason.strip()
    else:
        if not host.suspended_at:
            raise AppError(409, "NOT_SUSPENDED", "This host is not suspended.")
        host.suspended_at = None
        host.suspended_reason = None
    action = "host.suspended" if suspend else "host.reactivated"
    audit.record(session, action=action, entity_type="host", entity_id=host.id, actor_id=admin.id, reason=reason, ip=ip)
    owner = await session.get(User, host.user_id)
    enqueue_email(session, to_email=owner.email, template="host_suspended" if suspend else "host_reactivated",
                  context={"name": owner.full_name, "note": (reason or "").strip()}, user_id=owner.id)


# ---------- Stripe ----------

def apply_stripe_status(host: HostProfile, status: AccountStatus) -> bool:
    """Copies Stripe's view onto the host. Returns True when this made the host active for the first time."""
    host.stripe_details_submitted = status.details_submitted
    host.stripe_payouts_enabled = status.payouts_enabled
    host.stripe_transfers_active = status.transfers_active
    host.stripe_requirements = status.requirements
    host.stripe_checked_at = datetime.now(UTC)
    if host.review_status == REVIEW_APPROVED and stripe_ready(host) and host.activated_at is None:
        host.activated_at = datetime.now(UTC)
        return True
    return False


async def _notify_active(session: AsyncSession, host: HostProfile) -> None:
    owner = await session.get(User, host.user_id)
    audit.record(session, action="host.activated", entity_type="host", entity_id=host.id, actor_id=None)
    enqueue_email(session, to_email=owner.email, template="host_active", context={"name": owner.full_name}, user_id=owner.id)


async def onboarding_link(session: AsyncSession, host: HostProfile, user: User) -> str:
    if host.review_status != REVIEW_APPROVED:
        raise AppError(409, "NOT_APPROVED", "Payout setup opens after your application is approved.")
    if host.suspended_at:
        raise AppError(403, "HOST_SUSPENDED", "Your host account is suspended.")
    gateway = get_gateway()
    country = (host.application_data or {}).get("country", "AU")
    await _check_country(session, country)  # e.g. a host approved before the country list was tightened
    if host.stripe_account_id is None:
        host.stripe_account_id = await asyncio.to_thread(
            gateway.create_account, email=user.email, country=country, host_id=str(host.id)
        )
        await session.flush()
    base = get_settings().app_base_url.rstrip("/")
    return await asyncio.to_thread(
        gateway.create_onboarding_link, host.stripe_account_id, f"{base}/host/payouts?refresh=1", f"{base}/host/payouts?return=1"
    )


async def refresh_stripe(session: AsyncSession, host: HostProfile) -> None:
    if host.stripe_account_id is None:
        return
    status = await asyncio.to_thread(get_gateway().retrieve_account, host.stripe_account_id)
    if apply_stripe_status(host, status):
        await _notify_active(session, host)


async def dev_complete_stripe(session: AsyncSession, host: HostProfile, user: User) -> None:
    settings = get_settings()
    if settings.stripe_secret_key or settings.is_production:
        raise AppError(403, "NOT_AVAILABLE", "Only available with the fake Stripe gateway in development.")
    if host.review_status != REVIEW_APPROVED:
        raise AppError(409, "NOT_APPROVED", "Payout setup opens after your application is approved.")
    if host.stripe_account_id is None:
        await onboarding_link(session, host, user)
    if not host.stripe_account_id.endswith("_done"):
        host.stripe_account_id += "_done"
    await refresh_stripe(session, host)


async def handle_account_updated(session: AsyncSession, account: dict) -> None:
    """Webhook path: account.updated from Stripe."""
    from app.modules.payments.stripe_gateway import status_from_stripe_account

    host = (await session.execute(select(HostProfile).where(HostProfile.stripe_account_id == account.get("id")))).scalar_one_or_none()
    if host is None:
        return
    if apply_stripe_status(host, status_from_stripe_account(account)):
        await _notify_active(session, host)


# ---------- per-host setting overrides ----------

async def get_overrides(session: AsyncSession, host_id: uuid.UUID) -> dict[str, Any]:
    rows = (await session.execute(select(HostSettingOverride).where(HostSettingOverride.host_id == host_id))).scalars().all()
    return {r.key: r.value["v"] for r in rows}


async def set_override(session: AsyncSession, host: HostProfile, key: str, value: Any, *, reason: str | None, admin: User, ip: str | None) -> Any:
    definition = get_def(key)
    if not definition.host_override:
        raise AppError(422, "NOT_OVERRIDABLE", f"'{definition.label}' can only be set globally.")
    clean = definition.validate(value)
    row = (await session.execute(
        select(HostSettingOverride).where(HostSettingOverride.host_id == host.id, HostSettingOverride.key == key)
    )).scalar_one_or_none()
    before = row.value["v"] if row else None
    if row is None:
        session.add(HostSettingOverride(host_id=host.id, key=key, value={"v": clean}, reason=reason, created_by=admin.id))
    else:
        row.value = {"v": clean}
        row.reason = reason
        row.created_by = admin.id
    audit.record(session, action="host.override_set", entity_type="host", entity_id=host.id, actor_id=admin.id,
                 before={key: before}, after={key: clean}, reason=reason, ip=ip)
    return clean


async def clear_override(session: AsyncSession, host: HostProfile, key: str, *, admin: User, ip: str | None) -> None:
    get_def(key)
    row = (await session.execute(
        select(HostSettingOverride).where(HostSettingOverride.host_id == host.id, HostSettingOverride.key == key)
    )).scalar_one_or_none()
    if row is None:
        raise AppError(404, "NOT_FOUND", "No override set for that setting.")
    audit.record(session, action="host.override_cleared", entity_type="host", entity_id=host.id, actor_id=admin.id,
                 before={key: row.value["v"]}, after={key: None}, ip=ip)
    await session.delete(row)
