"""Email templates. Each entry: (subject, plain-text body, HTML body). Rendered with Jinja2 (autoescaped HTML)."""
from jinja2 import Environment, select_autoescape

_text_env = Environment(autoescape=False)
_html_env = Environment(autoescape=select_autoescape(default=True))

_HTML_WRAP = """<div style="font-family:Arial,sans-serif;max-width:520px;margin:auto;color:#111">
<h2 style="color:#e11d48">NearBNB</h2>{body}
<p style="color:#666;font-size:12px;margin-top:32px">If you did not expect this email you can ignore it.</p></div>"""

_CODE_BLOCK = '<p style="font-size:32px;letter-spacing:6px;font-weight:bold">{{ code }}</p>'

TEMPLATES: dict[str, tuple[str, str, str]] = {
    "verify_email": (
        "Verify your NearBNB email",
        "Hi {{ name }},\n\nYour NearBNB verification code is: {{ code }}\n\nIt expires in {{ minutes }} minutes.\n",
        "<p>Hi {{ name }},</p><p>Your verification code:</p>" + _CODE_BLOCK
        + "<p>It expires in {{ minutes }} minutes.</p>",
    ),
    "reset_password": (
        "Reset your NearBNB password",
        "Hi {{ name }},\n\nYour password reset code is: {{ code }}\n\nIt expires in {{ minutes }} minutes. "
        "If you did not request this, ignore this email.\n",
        "<p>Hi {{ name }},</p><p>Your password reset code:</p>" + _CODE_BLOCK
        + "<p>It expires in {{ minutes }} minutes.</p>",
    ),
    "change_email": (
        "Confirm your new NearBNB email",
        "Hi {{ name }},\n\nUse this code to confirm your new email address: {{ code }}\n\nIt expires in {{ minutes }} minutes.\n",
        "<p>Hi {{ name }},</p><p>Confirm your new email address with this code:</p>" + _CODE_BLOCK
        + "<p>It expires in {{ minutes }} minutes.</p>",
    ),
    "password_changed": (
        "Your NearBNB password was changed",
        "Hi {{ name }},\n\nYour password was just changed. If this was not you, reset your password immediately "
        "and contact support.\n",
        "<p>Hi {{ name }},</p><p>Your password was just changed. If this was not you, reset your password "
        "immediately and contact support.</p>",
    ),
    "account_deactivated": (
        "Your NearBNB account was deactivated",
        "Hi {{ name }},\n\nYour account has been deactivated as requested.\n",
        "<p>Hi {{ name }},</p><p>Your account has been deactivated as requested.</p>",
    ),
}


def render(template: str, context: dict) -> tuple[str, str, str]:
    subject, text, html = TEMPLATES[template]
    return (
        _text_env.from_string(subject).render(**context),
        _text_env.from_string(text).render(**context),
        _HTML_WRAP.replace("{body}", _html_env.from_string(html).render(**context)),
    )


def _note_block(prefix: str) -> tuple[str, str]:
    return (
        "{% if note %}\n" + prefix + " {{ note }}\n{% endif %}",
        "{% if note %}<p><strong>" + prefix + "</strong> {{ note }}</p>{% endif %}",
    )


_note_text, _note_html = _note_block("Message from the NearBNB team:")

TEMPLATES.update({
    "host_submitted": (
        "We received your host application",
        "Hi {{ name }},\n\nThanks for applying to host on NearBNB. We will review your application and email you the result.\n",
        "<p>Hi {{ name }},</p><p>Thanks for applying to host on NearBNB. We will review your application and email you the result.</p>",
    ),
    "host_approved": (
        "Your NearBNB host application was approved",
        "Hi {{ name }},\n\nGood news: your application was approved. The next step is payout setup (Stripe) so you can receive "
        "payments. Log in and open Host > Payouts." + _note_text + "\n",
        "<p>Hi {{ name }},</p><p>Good news: your application was approved. The next step is payout setup (Stripe) so you can "
        "receive payments. Log in and open Host &gt; Payouts.</p>" + _note_html,
    ),
    "host_rejected": (
        "Update on your NearBNB host application",
        "Hi {{ name }},\n\nUnfortunately we could not approve your host application." + _note_text + "\n",
        "<p>Hi {{ name }},</p><p>Unfortunately we could not approve your host application.</p>" + _note_html,
    ),
    "host_changes_requested": (
        "Changes needed on your NearBNB host application",
        "Hi {{ name }},\n\nWe need a few changes before we can approve your application." + _note_text
        + "\n\nLog in, update your application and submit it again.\n",
        "<p>Hi {{ name }},</p><p>We need a few changes before we can approve your application.</p>" + _note_html
        + "<p>Log in, update your application and submit it again.</p>",
    ),
    "host_active": (
        "You are now an active NearBNB host",
        "Hi {{ name }},\n\nYour payout setup is complete and your host account is active. You can now submit properties for approval.\n",
        "<p>Hi {{ name }},</p><p>Your payout setup is complete and your host account is active. You can now submit properties for approval.</p>",
    ),
    "host_suspended": (
        "Your NearBNB host account was suspended",
        "Hi {{ name }},\n\nYour host account has been suspended." + _note_text + "\n",
        "<p>Hi {{ name }},</p><p>Your host account has been suspended.</p>" + _note_html,
    ),
    "host_reactivated": (
        "Your NearBNB host account was reactivated",
        "Hi {{ name }},\n\nYour host account has been reactivated.\n",
        "<p>Hi {{ name }},</p><p>Your host account has been reactivated.</p>",
    ),
    "listing_approved": (
        "Your listing was approved: {{ title }}",
        "Hi {{ name }},\n\n{{ title }} was approved and is now live." + _note_text + "\n",
        "<p>Hi {{ name }},</p><p><strong>{{ title }}</strong> was approved and is now live.</p>" + _note_html,
    ),
    "listing_rejected": (
        "Your listing was not approved: {{ title }}",
        "Hi {{ name }},\n\n{{ title }} could not be approved." + _note_text + "\n",
        "<p>Hi {{ name }},</p><p><strong>{{ title }}</strong> could not be approved.</p>" + _note_html,
    ),
    "listing_changes_requested": (
        "Changes needed on your listing: {{ title }}",
        "Hi {{ name }},\n\nPlease update {{ title }} and submit it again." + _note_text + "\n",
        "<p>Hi {{ name }},</p><p>Please update <strong>{{ title }}</strong> and submit it again.</p>" + _note_html,
    ),
})
