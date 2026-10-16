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
