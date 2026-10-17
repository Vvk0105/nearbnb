import uuid

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import get_current_user
from app.core.errors import AppError
from app.core.ratelimit import client_ip, rate_limit
from app.modules.hosts import service
from app.modules.users.models import User

router = APIRouter(prefix="/host", tags=["host"])


class SubmitIn(BaseModel):
    accept_host_terms: bool


@router.get("")
async def my_host_profile(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    host = await service.get_host(session, user)
    if host is None:
        return {"state": "none"}
    return await service.serialize_host(session, host)


@router.post("/apply", status_code=201)
async def apply(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    host = await service.apply(session, user)
    await session.commit()
    return await service.serialize_host(session, host)


@router.patch("/application")
async def save_application(body: service.ApplicationDraft, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    host = await service.require_host(session, user)
    await service.save_draft(session, host, body)
    await session.commit()
    return await service.serialize_host(session, host)


@router.post("/documents", status_code=201, dependencies=[Depends(rate_limit("host_docs", 30, 3600))])
async def upload_document(
    doc_type: str = Form(...), file: UploadFile = File(...),
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
):
    host = await service.require_host(session, user)
    data = await file.read(service.MAX_DOC_BYTES + 1)
    await service.add_document(session, host, doc_type, file.filename or "document", data)
    await session.commit()
    return await service.serialize_host(session, host)


@router.delete("/documents/{doc_id}")
async def delete_document(doc_id: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    host = await service.require_host(session, user)
    try:
        did = uuid.UUID(doc_id)
    except ValueError:
        raise AppError(404, "NOT_FOUND") from None
    await service.delete_document(session, host, did)
    await session.commit()
    return await service.serialize_host(session, host)


@router.post("/application/submit")
async def submit_application(body: SubmitIn, request: Request, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    host = await service.require_host(session, user)
    await service.submit(session, host, user, accept_host_terms=body.accept_host_terms, ip=client_ip(request))
    await session.commit()
    from app.modules.notifications.service import kick_outbox

    kick_outbox()
    return await service.serialize_host(session, host)


@router.post("/stripe/onboarding-link", dependencies=[Depends(rate_limit("stripe_link", 20, 3600))])
async def stripe_onboarding_link(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    host = await service.require_host(session, user)
    url = await service.onboarding_link(session, host, user)
    await session.commit()
    return {"url": url}


@router.get("/stripe/status")
async def stripe_status(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    host = await service.require_host(session, user)
    await service.refresh_stripe(session, host)
    await session.commit()
    from app.modules.notifications.service import kick_outbox

    kick_outbox()
    return await service.serialize_host(session, host)


@router.post("/stripe/dev-complete")
async def stripe_dev_complete(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    """Development only (fake Stripe gateway): pretends the host finished Stripe onboarding."""
    host = await service.require_host(session, user)
    await service.dev_complete_stripe(session, host, user)
    await session.commit()
    return await service.serialize_host(session, host)
