"""Imports every model so Alembic autogenerate and Base.metadata see all tables."""
from app.modules.audit.models import AuditLog  # noqa: F401
from app.modules.auth.models import OtpCode, RefreshToken  # noqa: F401
from app.modules.legal.models import LegalDocument  # noqa: F401
from app.modules.notifications.models import EmailOutbox  # noqa: F401
from app.modules.settings.models import Setting  # noqa: F401
from app.modules.users.models import User, UserConsent, UserSocialLink  # noqa: F401
