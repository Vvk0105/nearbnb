"""How much a guest gets back when a booking is cancelled, and what the host keeps. Pure functions on whole cents.

The same function produces the preview a guest sees and the refund that is actually paid, so they cannot disagree."""
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal

from app.modules.pricing.service import money_round

COMPONENTS = ("accommodation", "cleaning", "service_fee")


@dataclass(frozen=True)
class RefundQuote:
    mode: str                 # "policy" (by the template) or "full" (host or admin cancelled)
    policy_name: str
    hours_before: float
    refund_pct: int
    accommodation: int
    cleaning_fee: int
    service_fee: int
    tax: int
    total: int                # what goes back to the guest
    host_accommodation: int   # what the host keeps of the accommodation
    host_cleaning_fee: int
    host_commission: int      # platform commission on what the host keeps
    host_payable: int         # what is paid out to the host

    def to_dict(self) -> dict:
        return asdict(self)


def hours_until(check_in: datetime, now: datetime) -> float:
    return (check_in - now).total_seconds() / 3600


def pct_for(tiers: list[dict], hours_before: float) -> int:
    """The refund percentage for cancelling `hours_before` hours ahead: the first tier (largest first) it reaches."""
    for tier in sorted(tiers, key=lambda t: t["hours_before"], reverse=True):
        if hours_before >= tier["hours_before"]:
            return int(tier["refund_pct"])
    return 0


def calculate_refund(*, pricing: dict, tiers: list[dict], policy_name: str, check_in: datetime, now: datetime,
                     mode: str = "policy", service_fee_refundable: bool = False) -> RefundQuote:
    """`pricing` is the booking's stored price snapshot. mode "full" refunds everything the guest paid."""
    rounding = pricing.get("rounding", "half_up")
    amounts = {"accommodation": pricing["accommodation"], "cleaning": pricing["cleaning_fee"], "service_fee": pricing["service_fee"]}
    hours = hours_until(check_in, now)

    if mode == "full":
        pct = 100
        refunded = dict(amounts)
    else:
        pct = pct_for(tiers, hours) if hours > 0 else 0
        refunded = {
            "accommodation": money_round(Decimal(amounts["accommodation"]) * pct / 100, rounding),
            "cleaning": money_round(Decimal(amounts["cleaning"]) * pct / 100, rounding),
            "service_fee": money_round(Decimal(amounts["service_fee"]) * pct / 100, rounding) if service_fee_refundable else 0,
        }

    tax = 0
    if pricing.get("tax") and not pricing.get("tax_inclusive"):  # inclusive tax is already inside the amounts above
        applies = pricing.get("tax_applies_to") or list(COMPONENTS)  # bookings made before this was stored taxed all three
        taxable = sum(amounts[c] for c in applies if c in amounts)
        refunded_taxable = sum(refunded[c] for c in applies if c in refunded)
        tax = pricing["tax"] if mode == "full" else (money_round(Decimal(pricing["tax"]) * refunded_taxable / taxable, rounding) if taxable else 0)

    total = min(sum(refunded.values()) + tax, pricing["total"])

    if mode == "full":
        kept_acc = kept_clean = commission = 0
    else:
        kept_acc = amounts["accommodation"] - refunded["accommodation"]
        kept_clean = amounts["cleaning"] - refunded["cleaning"]
        base = kept_acc + (kept_clean if pricing.get("commission_base") == "accommodation_and_cleaning" else 0)
        commission = money_round(Decimal(base) * Decimal(str(pricing.get("commission_percent", 0))) / 100, rounding)
    return RefundQuote(
        mode=mode, policy_name=policy_name, hours_before=round(hours, 2), refund_pct=pct,
        accommodation=refunded["accommodation"], cleaning_fee=refunded["cleaning"], service_fee=refunded["service_fee"], tax=tax, total=total,
        host_accommodation=kept_acc, host_cleaning_fee=kept_clean, host_commission=commission, host_payable=max(0, kept_acc + kept_clean - commission),
    )
