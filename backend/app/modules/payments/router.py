import json
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session
from app.core.errors import AppError
from app.modules.hosts import service as host_service
from app.modules.notifications.service import kick_outbox
from app.modules.payments import service as payment_service
from app.modules.payments.models import StripeEvent
from app.modules.payments.refunds import apply_refund_event
from app.modules.payments.stripe_gateway import intent_from_stripe, refund_from_stripe
from app.modules.payouts import service as payout_service

log = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])
REFUND_EVENTS = ("refund.created", "refund.updated", "refund.failed")
PAYOUT_EVENTS = ("payout.created", "payout.updated", "payout.paid", "payout.failed", "payout.canceled")
PAYMENT_EVENTS = ("payment_intent.succeeded", "payment_intent.payment_failed", "payment_intent.processing", "payment_intent.canceled")


@router.post("/stripe")
async def stripe_webhook(request: Request, session: AsyncSession = Depends(get_session)):
    """Stripe calls this directly (no cookies, so no CSRF). The signature is the authentication."""
    settings = get_settings()
    if not settings.stripe_webhook_secret:
        raise AppError(503, "WEBHOOK_NOT_CONFIGURED", "Stripe webhook secret is not set.")
    import stripe

    payload = await request.body()
    signature = request.headers.get("stripe-signature", "")
    # Stripe gives each webhook destination its own signing secret (for example one for "your account" events and
    # one for "connected accounts" events), so STRIPE_WEBHOOK_SECRET may hold several, separated by commas.
    secrets = [x.strip() for x in settings.stripe_webhook_secret.split(",") if x.strip()]
    verified = False
    for secret in secrets:
        try:
            stripe.Webhook.construct_event(payload, signature, secret)
            verified = True
            break
        except (ValueError, stripe.SignatureVerificationError):
            continue
    if not verified:
        raise AppError(400, "INVALID_SIGNATURE", "Invalid webhook signature.")

    event_dict = json.loads(payload)  # signature already verified against these exact bytes
    row = StripeEvent(event_id=event_dict["id"], type=event_dict["type"], payload=event_dict)
    session.add(row)
    try:
        await session.flush()
    except IntegrityError:  # already received: acknowledge so Stripe stops retrying
        await session.rollback()
        return {"received": True, "duplicate": True}

    try:
        if event_dict["type"] == "account.updated":
            await host_service.handle_account_updated(session, event_dict["data"]["object"])
        elif event_dict["type"] in REFUND_EVENTS:
            await apply_refund_event(session, refund_from_stripe(event_dict["data"]["object"]))
        elif event_dict["type"] in PAYOUT_EVENTS:
            await payout_service.record_payout(session, event_dict.get("account"), event_dict["data"]["object"])
        elif event_dict["type"] in PAYMENT_EVENTS:
            await payment_service.apply_intent(session, intent_from_stripe(event_dict["data"]["object"]))
        row.processed_at = datetime.now(UTC)
    except Exception as exc:  # noqa: BLE001
        log.exception("stripe webhook handling failed: %s", event_dict.get("id"))
        row.error = str(exc)[:500]
    await session.commit()
    kick_outbox()
    return {"received": True}
