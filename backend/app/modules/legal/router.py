from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.modules.legal import service

router = APIRouter(prefix="/legal", tags=["legal"])


@router.get("/{doc_type}")
async def get_document(doc_type: str, session: AsyncSession = Depends(get_session)):
    doc = await service.current_doc(session, doc_type)
    return {
        "doc_type": doc.doc_type,
        "version": doc.version,
        "title": doc.title,
        "body": doc.body,
        "published_at": doc.published_at,
    }
