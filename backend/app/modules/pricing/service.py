"""Price calculation. Pure functions on integer minor units (cents); no floats are used for money.

The same function produces the guest quote (this phase) and the stored booking snapshot (Phase 4), so what a
guest is shown is exactly what they are charged."""
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.pricing.models import TaxRule

ROUNDING = {"half_up": ROUND_HALF_UP, "half_even": ROUND_HALF_EVEN, "down": ROUND_DOWN}
TAX_COMPONENTS = ("accommodation", "cleaning", "service_fee")


@dataclass(frozen=True)
class TaxSpec:
    name: str
    rate_bp: int
    inclusive: bool
    applies_to: tuple[str, ...]


def money_round(value: Decimal, mode: str) -> int:
    return int(value.quantize(Decimal(1), rounding=ROUNDING.get(mode, ROUND_HALF_UP)))


def compute_price(
    *,
    price_per_24h: int,
    hours: int,
    cleaning_fee: int,
    service_fee_percent: float,
    tax: TaxSpec | None,
    commission_percent: float,
    commission_base: str = "accommodation",
    rounding: str = "half_up",
) -> dict:
    """Returns every component in cents. `host_payable` and `commission` are for internal use (booking snapshot);
    the guest-facing quote omits them."""
    nights = hours // 24
    accommodation = price_per_24h * nights
    service_fee = money_round(Decimal(accommodation) * Decimal(str(service_fee_percent)) / 100, rounding)

    tax_amount = 0
    if tax is not None and tax.rate_bp > 0:
        parts = {"accommodation": accommodation, "cleaning": cleaning_fee, "service_fee": service_fee}
        base = sum(parts[c] for c in tax.applies_to if c in parts)
        rate = Decimal(tax.rate_bp) / 10000
        if tax.inclusive:
            tax_amount = money_round(Decimal(base) - Decimal(base) / (1 + rate), rounding)  # portion already inside the price
        else:
            tax_amount = money_round(Decimal(base) * rate, rounding)

    subtotal = accommodation + cleaning_fee + service_fee
    total = subtotal if (tax is not None and tax.inclusive) else subtotal + tax_amount

    commission_amount_base = accommodation + (cleaning_fee if commission_base == "accommodation_and_cleaning" else 0)
    commission = money_round(Decimal(commission_amount_base) * Decimal(str(commission_percent)) / 100, rounding)

    return {
        "nights": nights,
        "hours": hours,
        "accommodation": accommodation,
        "cleaning_fee": cleaning_fee,
        "service_fee": service_fee,
        "service_fee_percent": service_fee_percent,
        "tax": tax_amount,
        "tax_name": tax.name if tax else None,
        "tax_rate_percent": (tax.rate_bp / 100) if tax else 0,
        "tax_inclusive": bool(tax and tax.inclusive),
        "total": total,
        "commission": commission,
        "commission_percent": commission_percent,
        "host_payable": accommodation + cleaning_fee - commission,
    }


GUEST_FIELDS = (
    "nights", "hours", "accommodation", "cleaning_fee", "service_fee", "service_fee_percent", "tax", "tax_name",
    "tax_rate_percent", "tax_inclusive", "total",
)


def guest_view(breakdown: dict) -> dict:
    return {k: breakdown[k] for k in GUEST_FIELDS}


async def tax_for(session: AsyncSession, country: str, region: str = "") -> TaxSpec | None:
    """The newest active rule for the region, falling back to the country-wide rule."""
    now = datetime.now(UTC)
    rows = (await session.execute(
        select(TaxRule).where(TaxRule.country == country.upper(), TaxRule.active.is_(True), TaxRule.effective_from <= now)
        .order_by(TaxRule.effective_from.desc())
    )).scalars().all()
    chosen = next((r for r in rows if region and r.region.lower() == region.lower()), None) or next((r for r in rows if not r.region), None)
    if chosen is None:
        return None
    return TaxSpec(chosen.name, chosen.rate_bp, chosen.inclusive, tuple(chosen.applies_to or TAX_COMPONENTS))


async def seed_tax_rules(session: AsyncSession) -> int:
    exists = (await session.execute(select(TaxRule).where(TaxRule.country == "AU", TaxRule.region == ""))).first()
    if exists:
        return 0
    session.add(TaxRule(country="AU", region="", name="GST", rate_bp=1000, inclusive=False, applies_to=list(TAX_COMPONENTS)))
    await session.commit()
    return 1
