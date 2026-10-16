import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.modules.audit import service as audit
from app.modules.legal.models import CONSENT_REQUIRED_FOR_ALL, DOC_TYPES, LegalDocument
from app.modules.users.models import User, UserConsent

PLACEHOLDER = (
    "PLACEHOLDER TEXT. Replace this with the legal text supplied by NearBNB's lawyer "
    "before launch (Admin > Legal documents)."
)
TITLES = {"terms": "Terms of Service", "privacy": "Privacy Policy", "host_terms": "Host Terms"}


async def current_doc(session: AsyncSession, doc_type: str) -> LegalDocument:
    if doc_type not in DOC_TYPES:
        raise AppError(404, "UNKNOWN_DOCUMENT")
    doc = (
        await session.execute(
            select(LegalDocument).where(LegalDocument.doc_type == doc_type).order_by(LegalDocument.version.desc()).limit(1)
        )
    ).scalar_one_or_none()
    if doc is None:
        raise AppError(404, "DOCUMENT_NOT_PUBLISHED", f"{TITLES[doc_type]} has not been published yet")
    return doc


async def current_versions(session: AsyncSession) -> dict[str, int]:
    rows = (
        await session.execute(select(LegalDocument.doc_type, func.max(LegalDocument.version)).group_by(LegalDocument.doc_type))
    ).all()
    return {doc_type: version for doc_type, version in rows}


async def seed_placeholders(session: AsyncSession) -> int:
    existing = await current_versions(session)
    created = 0
    for doc_type in DOC_TYPES:
        if doc_type not in existing:
            session.add(LegalDocument(doc_type=doc_type, version=1, title=TITLES[doc_type], body=PLACEHOLDER))
            created += 1
    await session.commit()
    return created


async def publish(
    session: AsyncSession, doc_type: str, title: str, body: str, *, actor_id: uuid.UUID, ip: str | None
) -> LegalDocument:
    if doc_type not in DOC_TYPES:
        raise AppError(404, "UNKNOWN_DOCUMENT")
    version = (await current_versions(session)).get(doc_type, 0) + 1
    doc = LegalDocument(doc_type=doc_type, version=version, title=title, body=body)
    session.add(doc)
    audit.record(
        session, action="legal.published", entity_type="legal_document", entity_id=f"{doc_type}:{version}",
        actor_id=actor_id, after={"doc_type": doc_type, "version": version, "title": title}, ip=ip,
    )
    await session.commit()
    return doc


async def accepted_versions(session: AsyncSession, user_id: uuid.UUID) -> dict[str, int]:
    rows = (
        await session.execute(
            select(UserConsent.doc_type, func.max(UserConsent.version))
            .where(UserConsent.user_id == user_id)
            .group_by(UserConsent.doc_type)
        )
    ).all()
    return {doc_type: version for doc_type, version in rows}


async def pending_consents(session: AsyncSession, user: User) -> list[dict]:
    """Documents (with newer versions than the user accepted) the user must accept again."""
    current = await current_versions(session)
    accepted = await accepted_versions(session, user.id)
    pending = []
    for doc_type in CONSENT_REQUIRED_FOR_ALL:
        if doc_type in current and accepted.get(doc_type, 0) < current[doc_type]:
            pending.append({"doc_type": doc_type, "version": current[doc_type], "title": TITLES[doc_type]})
    return pending


async def record_consent(session: AsyncSession, user_id: uuid.UUID, doc_type: str, ip: str | None) -> int:
    doc = await current_doc(session, doc_type)
    session.add(UserConsent(user_id=user_id, doc_type=doc_type, version=doc.version, ip=ip))
    return doc.version
