"""Stripe Connect access behind an interface.

* RealStripeGateway talks to Stripe (Express connected accounts, hosted onboarding).
* FakeStripeGateway is used automatically in development when STRIPE_SECRET_KEY is empty, so the whole host
  flow can be exercised without a Stripe account. It is refused in production.

NOTE: RealStripeGateway has not been exercised against Stripe yet (no keys at the time of writing). Test it
with Stripe test-mode keys before relying on it.
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


class StripeGateway(Protocol):
    is_fake: bool

    def create_account(self, *, email: str, country: str, host_id: str) -> str: ...
    def create_onboarding_link(self, account_id: str, refresh_url: str, return_url: str) -> str: ...
    def retrieve_account(self, account_id: str) -> AccountStatus: ...


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

    def create_account(self, *, email: str, country: str, host_id: str) -> str:
        import stripe

        try:
            account = self._client.accounts.create(
                params={
                    "type": "express",
                    "country": country,
                    "email": email,
                    # Separate charges and transfers: we collect from the guest and later transfer the host's share.
                    "capabilities": {"transfers": {"requested": True}},
                    "metadata": {"host_id": host_id},
                }
            )
        except stripe.StripeError as exc:
            raise _readable(exc) from exc
        return account.id

    def create_onboarding_link(self, account_id: str, refresh_url: str, return_url: str) -> str:
        import stripe

        try:
            link = self._client.account_links.create(
                params={"account": account_id, "refresh_url": refresh_url, "return_url": return_url, "type": "account_onboarding"}
            )
        except stripe.StripeError as exc:
            raise _readable(exc) from exc
        return link.url

    def retrieve_account(self, account_id: str) -> AccountStatus:
        import stripe

        try:
            return status_from_stripe_account(self._client.accounts.retrieve(account_id))
        except stripe.StripeError as exc:
            raise _readable(exc) from exc


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

    def create_account(self, *, email: str, country: str, host_id: str) -> str:
        return f"acct_fake_{uuid.uuid4().hex[:12]}"

    def create_onboarding_link(self, account_id: str, refresh_url: str, return_url: str) -> str:
        return f"{return_url}{'&' if '?' in return_url else '?'}fake=1"

    def retrieve_account(self, account_id: str) -> AccountStatus:
        if account_id.endswith("_done"):
            return AccountStatus(True, True, True, [])
        return AccountStatus(False, False, False, ["external_account", "individual.verification.document"])


def get_gateway() -> StripeGateway:
    settings = get_settings()
    if settings.stripe_secret_key:
        return RealStripeGateway(settings.stripe_secret_key)
    if settings.is_production:
        raise AppError(503, "STRIPE_NOT_CONFIGURED", "Payout setup is temporarily unavailable.")
    log.warning("STRIPE_SECRET_KEY is empty: using the fake Stripe gateway (development only)")
    return FakeStripeGateway()
