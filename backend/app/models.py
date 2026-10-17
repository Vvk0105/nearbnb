"""Imports every model so Alembic autogenerate and Base.metadata see all tables."""
from app.modules.audit.models import AuditLog  # noqa: F401
from app.modules.auth.models import OtpCode, RefreshToken  # noqa: F401
from app.modules.bookings.models import Booking  # noqa: F401
from app.modules.hosts.models import HostDocument, HostProfile, HostSettingOverride  # noqa: F401
from app.modules.legal.models import LegalDocument  # noqa: F401
from app.modules.payments.models import Invoice, Payment, Refund, RefundRequest, StripeEvent  # noqa: F401
from app.modules.cancellations.models import CancellationTemplate  # noqa: F401
from app.modules.payouts.models import PayoutRecord, Transfer  # noqa: F401
from app.modules.bookings.models import BookingEvent  # noqa: F401
from app.modules.pricing.models import TaxRule  # noqa: F401
from app.modules.properties.models import (  # noqa: F401
    Amenity, ListingRevision, Media, Property, PropertyType, Room, RoomOccupancy, Unit, unit_amenities,
)
from app.modules.notifications.models import EmailOutbox  # noqa: F401
from app.modules.settings.models import Setting  # noqa: F401
from app.modules.users.models import User, UserConsent, UserSocialLink  # noqa: F401
