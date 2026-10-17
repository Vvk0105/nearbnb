"""Catalogue of every admin-configurable setting with its default and validation rules.

Adding a setting = adding one entry here. The admin UI is generated from this registry.
"""
from dataclasses import dataclass, field, replace
from typing import Any

from app.core.errors import AppError


@dataclass(frozen=True)
class SettingDef:
    key: str
    group: str
    label: str
    type: str  # int | float | bool | str | choice | str_list | json
    default: Any
    description: str = ""
    min: float | None = None
    max: float | None = None
    choices: tuple[Any, ...] = field(default_factory=tuple)
    unit: str = ""
    host_override: bool = False  # admin may set a different value for an individual host

    def validate(self, value: Any) -> Any:
        def bad(msg: str) -> AppError:
            return AppError(422, "INVALID_SETTING", f"{self.label}: {msg}")

        if self.type == "int":
            if isinstance(value, bool) or not isinstance(value, int):
                raise bad("must be a whole number")
        elif self.type == "float":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise bad("must be a number")
            value = float(value)
        elif self.type == "bool":
            if not isinstance(value, bool):
                raise bad("must be true or false")
        elif self.type == "str":
            if not isinstance(value, str) or not value.strip():
                raise bad("must be non-empty text")
            value = value.strip()
        elif self.type == "choice":
            if value not in self.choices:
                raise bad(f"must be one of {list(self.choices)}")
        elif self.type == "str_list":
            if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
                raise bad("must be a list of text values")
            value = [v.strip() for v in value]
            if self.choices and not all(v in self.choices for v in value):
                raise bad(f"values must be from {list(self.choices)}")
        elif self.type == "json":
            if not isinstance(value, dict):
                raise bad("must be an object")
        if self.type in ("int", "float"):
            if self.min is not None and value < self.min:
                raise bad(f"must be at least {self.min}")
            if self.max is not None and value > self.max:
                raise bad(f"must be at most {self.max}")
        return value


_DEFS: list[SettingDef] = [
    # Booking rules
    SettingDef("checkin.interval_minutes", "Booking", "Check-in interval", "choice", 60,
               "Check-in times are offered at this step.", choices=(15, 30, 60), unit="min"),
    SettingDef("booking.host_response_minutes", "Booking", "Host response window", "int", 30,
               "Time a host has to accept or reject a request before it expires.", min=5, max=2880, unit="min"),
    SettingDef("booking.payment_deadline_minutes", "Booking", "Guest payment deadline", "int", 30,
               "Time a guest has to pay after the host accepts.", min=5, max=2880, unit="min"),
    SettingDef("booking.min_lead_time_hours", "Booking", "Minimum lead time", "int", 2,
               "Earliest check-in is this many hours from now.", min=0, max=720, unit="h"),
    SettingDef("booking.max_advance_days", "Booking", "Maximum advance window", "int", 180,
               "Furthest ahead a check-in can be booked.", min=1, max=730, unit="days"),
    SettingDef("booking.max_stay_hours", "Booking", "Maximum stay", "int", 168,
               "Longest allowed stay. Must be a multiple of the duration step.", min=24, max=2160, unit="h"),
    SettingDef("booking.duration_step_hours", "Booking", "Stay duration step", "int", 24,
               "Stays are multiples of this many hours.", min=24, max=24, unit="h"),
    SettingDef("booking.max_pending_requests", "Booking", "Max pending requests per guest", "int", 3,
               "Stops one guest holding many slots at once.", min=1, max=50),
    # Finance
    SettingDef("finance.commission_percent", "Finance", "Platform commission", "float", 12.0,
               "Taken from the host. PLACEHOLDER until the business sets the rate.", min=0, max=100, unit="%"),
    SettingDef("finance.commission_base", "Finance", "Commission base", "choice", "accommodation",
               "Amount the commission is calculated on.", choices=("accommodation", "accommodation_and_cleaning")),
    SettingDef("finance.service_fee_percent", "Finance", "Guest service fee", "float", 7.5,
               "Charged to the guest on the accommodation amount. PLACEHOLDER.", min=0, max=100, unit="%"),
    SettingDef("finance.service_fee_refundable", "Finance", "Service fee refundable", "bool", False,
               "Whether the guest service fee is refunded on cancellation."),
    SettingDef("finance.rounding", "Finance", "Rounding rule", "choice", "half_up",
               "How money is rounded to the smallest unit.", choices=("half_up", "half_even", "down")),
    # Payouts
    SettingDef("payout.anchor", "Payouts", "Payout anchor", "choice", "check_in",
               "Payout timer starts from check-in or check-out.", choices=("check_in", "check_out")),
    SettingDef("payout.delay_hours", "Payouts", "Payout delay", "int", 24,
               "Hours after the anchor before the host becomes eligible for payout.", min=0, max=720, unit="h"),
    # Cancellation
    SettingDef("cancellation.default_template", "Cancellation", "Default cancellation template", "str", "moderate",
               "Template key used when a host has not chosen one."),
    SettingDef("cancellation.guardrail_template", "Cancellation", "Strictest allowed template", "str", "strict",
               "Hosts may not customise a policy to be harsher than this template."),
    # Listings
    SettingDef("listing.reapproval_fields", "Listings", "Edits that need re-approval", "str_list",
               ["address", "property_type", "photos", "description"],
               "Editing any of these sends the listing back to admin review.",
               choices=("address", "property_type", "photos", "description", "price", "capacity",
                        "amenities", "cancellation_policy", "name", "rules")),
    SettingDef("listing.min_photos", "Listings", "Minimum photos per property", "int", 3,
               "A property needs at least this many photos before it can be submitted.", min=0, max=50),
    SettingDef("listing.min_unit_photos", "Listings", "Minimum photos per bookable unit", "int", 1,
               "Each unit needs at least this many photos before submission.", min=0, max=50),
    SettingDef("listing.max_photos", "Listings", "Max photos per listing", "int", 20, min=1, max=100),
    SettingDef("listing.max_photo_mb", "Listings", "Max photo size", "int", 8, min=1, max=25, unit="MB"),
    # Guest verification
    SettingDef("guest.required_fields", "Guest verification", "Required guest fields", "str_list",
               ["full_name", "dob", "phone", "social_url"],
               "Fields a guest must have before sending a booking request.",
               choices=("full_name", "dob", "phone", "social_url")),
    SettingDef("guest.min_social_profiles", "Guest verification", "Minimum social profiles", "int", 1, min=0, max=5),
    SettingDef("guest.allowed_social_domains", "Guest verification", "Accepted social platforms", "str_list",
               ["instagram.com", "facebook.com", "linkedin.com", "x.com", "twitter.com"],
               "Social profile URLs must belong to one of these domains."),
    SettingDef("guest.min_age", "Guest verification", "Minimum guest age", "int", 18, min=13, max=99, unit="years"),
    # Reviews and alerts
    SettingDef("review.window_days", "Reviews", "Review window", "int", 14,
               "Days after completion that reviews can be submitted.", min=1, max=90, unit="days"),
    SettingDef("alerts.host_cancellation_count", "Alerts", "Host cancellation alert: count", "int", 3, min=1, max=100),
    SettingDef("alerts.host_cancellation_days", "Alerts", "Host cancellation alert: window", "int", 30,
               min=1, max=365, unit="days"),
    # Hosts
    SettingDef("host.supported_countries", "Hosts", "Countries hosts can be in", "str_list", ["AU"],
               "Two-letter country codes. Host payouts only work for countries your Stripe account can pay out to "
               "(with separate charges and transfers, hosts must be in the same country as the platform's Stripe account)."),
    # Invoices (printed on every receipt / tax invoice; confirm the wording with your accountant)
    SettingDef("invoice.seller_name", "Invoices", "Seller name on invoices", "str", "NearBnB",
               "Legal name of the business issuing the invoice."),
    SettingDef("invoice.seller_tax_id", "Invoices", "Seller tax number (ABN)", "str", "ABN to be configured",
               "Tax registration number printed on tax invoices."),
    SettingDef("invoice.seller_address", "Invoices", "Seller address on invoices", "str", "Address to be configured"),
    SettingDef("invoice.footer_note", "Invoices", "Invoice footer", "str", "Thank you for booking with NearBnB."),
    # Calendar sync
    SettingDef("ical.poll_minutes", "Calendar sync", "iCal import interval", "int", 15, min=5, max=240, unit="min"),
]

# Settings an admin can override for a single host (everything else is global only).
HOST_OVERRIDABLE = {
    "booking.host_response_minutes", "booking.payment_deadline_minutes", "booking.min_lead_time_hours",
    "booking.max_advance_days", "finance.commission_percent", "payout.anchor", "payout.delay_hours",
}

REGISTRY: dict[str, SettingDef] = {d.key: replace(d, host_override=d.key in HOST_OVERRIDABLE) for d in _DEFS}


def get_def(key: str) -> SettingDef:
    try:
        return REGISTRY[key]
    except KeyError:
        raise AppError(404, "UNKNOWN_SETTING", f"Unknown setting '{key}'") from None
