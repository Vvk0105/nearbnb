"""Stripe Connect access behind an interface.

* RealStripeGateway talks to Stripe using the Accounts v2 API: a host is a v2 Account with the `recipient`
  configuration (receives transfers from the platform), the Express dashboard, and the platform responsible for
  fees and losses. That is the "separate charges and transfers" model.
* FakeStripeGateway is used automatically in development when STRIPE_SECRET_KEY is empty, so the whole host
  flow can be exercised without a Stripe account. It is refused in production.

Accounts created before the v2 migration (v1 Express accounts) keep working: Stripe lets v2 endpoints read them,
and v1 endpoints read v2 accounts. Where one API cannot serve an account we fall back to the other (see below).
"""
import logging
import uuid
from dataclasses import dataclass, field
from typing import Protocol

from app.core.config import get_settings
from app.core.errors import AppError

log = logging.getLogger(__name__)


@dataclass
class AccountStatus:
    details_submitted: bool = False
    payouts_enabled: bool = False
    transfers_active: bool = False
    requirements: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return self.details_submitted and self.payouts_enabled and self.transfers_active


@dataclass
class IntentInfo:
    """What the platform needs to know about a PaymentIntent (from the API, a webhook payload, or the fake gateway)."""

    id: str
    status: str = "requires_payment_method"
    client_secret: str | None = None
    amount: int = 0
    currency: str = ""
    charge_id: str | None = None
    failure_code: str | None = None
    failure_message: str | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class RefundInfo:
    id: str
    status: str = "succeeded"  # succeeded | pending | requires_action | failed | canceled
    failure_reason: str | None = None


class StripeGateway(Protocol):
    is_fake: bool

    def create_account(self, *, email: str, country: str, host_id: str, display_name: str = "") -> str: ...
    def create_onboarding_link(self, account_id: str, refresh_url: str, return_url: str) -> str: ...
    def retrieve_account(self, account_id: str) -> AccountStatus: ...
    def create_payment_intent(self, *, amount: int, currency: str, booking_id: str, reference: str, description: str, idempotency_key: str) -> IntentInfo: ...
    def retrieve_payment_intent(self, intent_id: str) -> IntentInfo: ...
    def cancel_payment_intent(self, intent_id: str) -> bool: ...
    def refund_payment(self, *, intent_id: str, amount: int, reason: str, idempotency_key: str) -> RefundInfo: ...
    def retrieve_refund(self, refund_id: str) -> RefundInfo: ...
    def create_transfer(self, *, amount: int, currency: str, destination: str, source_transaction: str | None, transfer_group: str, idempotency_key: str) -> str: ...
    def reverse_transfer(self, *, transfer_id: str, amount: int, idempotency_key: str) -> str: ...


def _readable(exc: Exception) -> AppError:
    """A Stripe failure becomes a clear message for the user. Full detail is logged, and shown outside production."""
    log.exception("Stripe request failed")
    detail = ""
    if not get_settings().is_production:
        detail = f" ({getattr(exc, 'user_message', None) or exc})"
    return AppError(
        502, "STRIPE_ERROR",
        "We couldn't complete the payout setup with Stripe right now. Please try again or contact support." + detail,
    )


class RealStripeGateway:
    is_fake = False

    def __init__(self, api_key: str):
        import stripe

        self._client = stripe.StripeClient(api_key)

    def create_account(self, *, email: str, country: str, host_id: str, display_name: str = "") -> str:
        import stripe

        try:
            account = self._client.v2.core.accounts.create(
                params={
                    "contact_email": email,
                    "display_name": display_name or email,
                    "dashboard": "express",
                    "identity": {"country": country.lower()},
                    # Separate charges and transfers: we collect from the guest and later transfer the host's share.
                    "configuration": {"recipient": {"capabilities": {"stripe_balance": {"stripe_transfers": {"requested": True}}}}},
                    # With the Express dashboard the platform carries fees and losses (same as the old v1 Express accounts).
                    "defaults": {"responsibilities": {"fees_collector": "application", "losses_collector": "application"}},
                    "metadata": {"host_id": host_id},
                }
            )
        except stripe.StripeError as exc:
            raise _readable(exc) from exc
        return account.id

    def create_onboarding_link(self, account_id: str, refresh_url: str, return_url: str) -> str:
        import stripe

        try:
            link = self._client.v2.core.account_links.create(
                params={
                    "account": account_id,
                    "use_case": {"type": "account_onboarding", "account_onboarding": {"refresh_url": refresh_url, "return_url": return_url}},
                }
            )
            return link.url
        except stripe.InvalidRequestError as exc:
            # Stripe refuses a v2 link for some accounts (accounts created before the v2 migration, and Stripe's older
            # test mode, which asks for a "sandbox"). The v1 link endpoint accepts every account, so use it instead.
            log.warning("v2 account link refused (%s); using the v1 account link", exc)
        except stripe.StripeError as exc:
            raise _readable(exc) from exc
        try:
            v1 = self._client.v1.account_links.create(
                params={"account": account_id, "refresh_url": refresh_url, "return_url": return_url, "type": "account_onboarding"}
            )
        except stripe.StripeError as exc:
            raise _readable(exc) from exc
        return v1.url

    def retrieve_account(self, account_id: str) -> AccountStatus:
        import stripe

        try:
            v2 = self._client.v2.core.accounts.retrieve(account_id, params={"include": ["configuration.recipient", "requirements"]})
            status = status_from_v2_account(v2)
            if status is not None:
                return status
            # An account without the recipient configuration (an older v1 account): read it the v1 way.
            return status_from_stripe_account(self._client.v1.accounts.retrieve(account_id))
        except stripe.StripeError as exc:
            raise _readable(exc) from exc


    # ---- payments: the platform charges the guest; the host's share is transferred later (Phase 6) ----

    def create_payment_intent(self, *, amount: int, currency: str, booking_id: str, reference: str, description: str, idempotency_key: str) -> IntentInfo:
        import stripe

        try:
            intent = self._client.v1.payment_intents.create(
                params={
                    "amount": amount,
                    "currency": currency.lower(),
                    "automatic_payment_methods": {"enabled": True},
                    "transfer_group": reference,  # ties the later transfer to the host to this charge
                    "description": description,
                    "metadata": {"booking_id": booking_id, "reference": reference},
                },
                options={"idempotency_key": idempotency_key},
            )
        except stripe.StripeError as exc:
            raise _readable_payment(exc) from exc
        return intent_from_stripe(intent)

    def retrieve_payment_intent(self, intent_id: str) -> IntentInfo:
        import stripe

        try:
            return intent_from_stripe(self._client.v1.payment_intents.retrieve(intent_id))
        except stripe.StripeError as exc:
            raise _readable_payment(exc) from exc

    def cancel_payment_intent(self, intent_id: str) -> bool:
        """Best effort: a payment that is already processing or paid cannot be cancelled (False)."""
        import stripe

        try:
            self._client.v1.payment_intents.cancel(intent_id)
            return True
        except stripe.StripeError:
            log.warning("could not cancel payment intent %s", intent_id, exc_info=True)
            return False

    def refund_payment(self, *, intent_id: str, amount: int, reason: str, idempotency_key: str) -> RefundInfo:
        import stripe

        try:
            refund = self._client.v1.refunds.create(
                params={"payment_intent": intent_id, "amount": amount, "reason": "requested_by_customer", "metadata": {"reason": reason[:400]}},
                options={"idempotency_key": idempotency_key},
            )
        except stripe.StripeError as exc:
            raise _readable_payment(exc) from exc
        return refund_from_stripe(refund)

    def retrieve_refund(self, refund_id: str) -> RefundInfo:
        import stripe

        try:
            return refund_from_stripe(self._client.v1.refunds.retrieve(refund_id))
        except stripe.StripeError as exc:
            raise _readable_payment(exc) from exc

    # ---- paying the host: a transfer from the platform balance to the host's connected account ----

    def create_transfer(self, *, amount: int, currency: str, destination: str, source_transaction: str | None, transfer_group: str, idempotency_key: str) -> str:
        import stripe

        params = {"amount": amount, "currency": currency.lower(), "destination": destination, "transfer_group": transfer_group}
        if source_transaction:
            params["source_transaction"] = source_transaction  # funds come from this charge even before they are available
        try:
            transfer = self._client.v1.transfers.create(params=params, options={"idempotency_key": idempotency_key})
        except stripe.StripeError as exc:
            raise _readable_payment(exc) from exc
        return transfer.id

    def reverse_transfer(self, *, transfer_id: str, amount: int, idempotency_key: str) -> str:
        import stripe

        try:
            reversal = self._client.v1.transfers.reversals.create(transfer_id, params={"amount": amount}, options={"idempotency_key": idempotency_key})
        except stripe.StripeError as exc:
            raise _readable_payment(exc) from exc
        return reversal.id


def _readable_payment(exc: Exception) -> AppError:
    log.exception("Stripe payment request failed")
    detail = ""
    if not get_settings().is_production:
        detail = f" ({getattr(exc, 'user_message', None) or exc})"
    return AppError(502, "STRIPE_ERROR", "We couldn't reach the payment provider right now. Please try again in a moment." + detail)


def intent_from_stripe(obj) -> IntentInfo:
    """Works for a PaymentIntent from the API and for the dict inside a webhook event."""
    data = _plain(obj) or {}
    charge = data.get("latest_charge")
    error = data.get("last_payment_error") or {}
    return IntentInfo(
        id=data.get("id", ""), status=data.get("status", ""), client_secret=data.get("client_secret"),
        amount=int(data.get("amount") or 0), currency=(data.get("currency") or "").upper(),
        charge_id=charge.get("id") if isinstance(charge, dict) else charge,
        failure_code=error.get("decline_code") or error.get("code"), failure_message=error.get("message"),
        metadata=dict(data.get("metadata") or {}),
    )


def refund_from_stripe(obj) -> RefundInfo:
    """Works for a Refund from the API and for the dict inside a webhook event."""
    data = _plain(obj) if hasattr(obj, "to_dict") or isinstance(obj, dict) else {"id": getattr(obj, "id", ""), "status": getattr(obj, "status", "succeeded")}
    return RefundInfo(id=data.get("id", ""), status=data.get("status") or "succeeded", failure_reason=data.get("failure_reason"))


def _plain(obj):
    """Stripe objects and plain dicts (webhook payloads) are read the same way."""
    return obj.to_dict() if hasattr(obj, "to_dict") else obj


def status_from_v2_account(account) -> AccountStatus | None:
    """Reads a v2 Account (retrieved with include=configuration.recipient,requirements).

    Returns None when the account has no recipient configuration, so the caller can read it another way.
    * transfers_active / payouts_enabled: the two recipient capabilities are `active`
    * requirements: what the host still has to provide (entries Stripe is waiting on the host for)
    * details_submitted: nothing the host owes is holding back a capability"""
    data = _plain(account) or {}
    recipient = (data.get("configuration") or {}).get("recipient")
    if not recipient:
        return None
    balance = (recipient.get("capabilities") or {}).get("stripe_balance") or {}
    entries = (data.get("requirements") or {}).get("entries") or []
    from_host = [e for e in entries if e.get("awaiting_action_from") == "user"]
    # Only items that hold back a capability count. (A new account lists everything as "eventually due" for a moment and
    # "past due" a moment later, so the deadline status is not a reliable signal.)
    blocking = [e for e in from_host if (e.get("impact") or {}).get("restricts_capabilities")]
    return AccountStatus(
        details_submitted=not blocking,
        payouts_enabled=(balance.get("payouts") or {}).get("status") == "active",
        transfers_active=(balance.get("stripe_transfers") or {}).get("status") == "active",
        requirements=[e.get("description", "") for e in from_host],
    )


def status_from_stripe_account(account) -> AccountStatus:
    """Works for both a retrieved Account object and the dict inside an account.updated webhook."""

    def get(obj, key, default=None):
        if obj is None:
            return default
        return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)

    requirements = get(account, "requirements")
    due = list(get(requirements, "currently_due", []) or [])
    transfers = get(get(account, "capabilities"), "transfers")
    return AccountStatus(
        details_submitted=bool(get(account, "details_submitted", False)),
        payouts_enabled=bool(get(account, "payouts_enabled", False)),
        transfers_active=transfers == "active",
        requirements=due,
    )


class FakeStripeGateway:
    """Stateless: an account id ending in '_done' is a fully onboarded account."""

    is_fake = True

    def create_account(self, *, email: str, country: str, host_id: str, display_name: str = "") -> str:
        return f"acct_fake_{uuid.uuid4().hex[:12]}"

    def create_onboarding_link(self, account_id: str, refresh_url: str, return_url: str) -> str:
        return f"{return_url}{'&' if '?' in return_url else '?'}fake=1"

    def retrieve_account(self, account_id: str) -> AccountStatus:
        if account_id.endswith("_done"):
            return AccountStatus(True, True, True, [])
        return AccountStatus(False, False, False, ["external_account", "individual.verification.document"])

    def create_payment_intent(self, *, amount: int, currency: str, booking_id: str, reference: str, description: str, idempotency_key: str) -> IntentInfo:
        # Deterministic from the idempotency key, so asking twice gives the same intent, like Stripe does.
        token = uuid.uuid5(uuid.NAMESPACE_URL, idempotency_key).hex[:20]
        return IntentInfo(id=f"pi_fake_{token}", client_secret=f"pi_fake_{token}_secret_fake", amount=amount, currency=currency.upper(),
                          metadata={"booking_id": booking_id, "reference": reference})

    def retrieve_payment_intent(self, intent_id: str) -> IntentInfo:
        return IntentInfo(id=intent_id, client_secret=f"{intent_id}_secret_fake")  # never paid: dev-complete simulates payment

    def cancel_payment_intent(self, intent_id: str) -> bool:
        return True

    def refund_payment(self, *, intent_id: str, amount: int, reason: str, idempotency_key: str) -> RefundInfo:
        return RefundInfo(id=f"re_fake_{uuid.uuid5(uuid.NAMESPACE_URL, idempotency_key).hex[:20]}")

    def retrieve_refund(self, refund_id: str) -> RefundInfo:
        return RefundInfo(id=refund_id)

    def create_transfer(self, *, amount: int, currency: str, destination: str, source_transaction: str | None, transfer_group: str, idempotency_key: str) -> str:
        return f"tr_fake_{uuid.uuid5(uuid.NAMESPACE_URL, idempotency_key).hex[:20]}"

    def reverse_transfer(self, *, transfer_id: str, amount: int, idempotency_key: str) -> str:
        return f"trr_fake_{uuid.uuid5(uuid.NAMESPACE_URL, idempotency_key).hex[:20]}"


def get_gateway() -> StripeGateway:
    settings = get_settings()
    if settings.stripe_secret_key:
        return RealStripeGateway(settings.stripe_secret_key)
    if settings.is_production:
        raise AppError(503, "STRIPE_NOT_CONFIGURED", "Payout setup is temporarily unavailable.")
    log.warning("STRIPE_SECRET_KEY is empty: using the fake Stripe gateway (development only)")
    return FakeStripeGateway()
