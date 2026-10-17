"""Email templates for booking requests. Merged into TEMPLATES by templates.py."""

_DETAILS_TEXT = (
    "\n{{ title }}\nCheck-in: {{ checkin }}\nCheck-out: {{ checkout }} (times at the property, {{ tz }})\n"
    "Guests: {{ guests }}\nReference: {{ reference }}\n"
)
_DETAILS_HTML = (
    "<p><strong>{{ title }}</strong><br>Check-in: {{ checkin }}<br>Check-out: {{ checkout }} (times at the property, {{ tz }})"
    "<br>Guests: {{ guests }}<br>Reference: {{ reference }}</p>"
)


def _button(label: str, path: str) -> str:
    return (f'<p><a href="{{{{ url }}}}{path}" style="background:#0d9488;color:#fff;padding:10px 18px;'
            f'border-radius:8px;text-decoration:none">{label}</a></p>')


BOOKING_TEMPLATES: dict[str, tuple[str, str, str]] = {
    "booking_request_host": (
        "New booking request: {{ title }}",
        "Hi {{ name }},\n\n{{ guest_name }} would like to stay." + _DETAILS_TEXT
        + "You would receive {{ host_payable }} (before the payout delay).\n\nPlease accept or decline before {{ expires }} "
        "({{ response_minutes }} minutes). If you do not respond the request expires and the dates are released.\n\n{{ url }}/host/requests\n",
        "<p>Hi {{ name }},</p><p><strong>{{ guest_name }}</strong> would like to stay.</p>" + _DETAILS_HTML
        + "<p>You would receive <strong>{{ host_payable }}</strong> (before the payout delay).</p>"
        "<p>Please accept or decline before <strong>{{ expires }}</strong> ({{ response_minutes }} minutes). If you do not respond "
        "the request expires and the dates are released.</p>" + _button("Review the request", "/host/requests"),
    ),
    "booking_request_guest": (
        "Request sent: {{ title }}",
        "Hi {{ name }},\n\nYour request was sent to the host." + _DETAILS_TEXT + "Total if accepted: {{ total }}\n\n"
        "The host has {{ response_minutes }} minutes to reply (until {{ expires }}). You have not been charged. "
        "We will email you as soon as they respond.\n\n{{ url }}/account/trips\n",
        "<p>Hi {{ name }},</p><p>Your request was sent to the host.</p>" + _DETAILS_HTML
        + "<p>Total if accepted: <strong>{{ total }}</strong></p>"
        "<p>The host has {{ response_minutes }} minutes to reply (until {{ expires }}). You have not been charged. "
        "We will email you as soon as they respond.</p>" + _button("View my request", "/account/trips"),
    ),
    "booking_reminder_host": (
        "Reminder: a booking request is waiting for you",
        "Hi {{ name }},\n\n{{ guest_name }}'s request is still waiting." + _DETAILS_TEXT
        + "\nIt expires at {{ expires }}.\n\n{{ url }}/host/requests\n",
        "<p>Hi {{ name }},</p><p><strong>{{ guest_name }}</strong>'s request is still waiting.</p>" + _DETAILS_HTML
        + "<p>It expires at <strong>{{ expires }}</strong>.</p>" + _button("Review the request", "/host/requests"),
    ),
    "booking_accepted_guest": (
        "Your request was accepted: {{ title }}",
        "Hi {{ name }},\n\nGood news, the host accepted your request." + _DETAILS_TEXT + "Total: {{ total }}\n\n"
        "To keep the dates, pay before {{ pay_by }} ({{ payment_minutes }} minutes). After that the dates are released.\n\n{{ url }}/account/trips\n",
        "<p>Hi {{ name }},</p><p>Good news, the host accepted your request.</p>" + _DETAILS_HTML
        + "<p>Total: <strong>{{ total }}</strong></p>"
        "<p>To keep the dates, pay before <strong>{{ pay_by }}</strong> ({{ payment_minutes }} minutes). "
        "After that the dates are released.</p>" + _button("Go to my trip", "/account/trips"),
    ),
    "booking_rejected_guest": (
        "Your request was declined: {{ title }}",
        "Hi {{ name }},\n\nSorry, the host could not accept your request." + _DETAILS_TEXT
        + "{% if note %}\nHost's message: {{ note }}\n{% endif %}\nYou were not charged. You can search for other places on NearBNB.\n",
        "<p>Hi {{ name }},</p><p>Sorry, the host could not accept your request.</p>" + _DETAILS_HTML
        + "{% if note %}<p><strong>Host's message:</strong> {{ note }}</p>{% endif %}"
        "<p>You were not charged. You can search for other places on NearBNB.</p>",
    ),
    "booking_withdrawn_host": (
        "Request withdrawn: {{ title }}",
        "Hi {{ name }},\n\n{{ guest_name }} withdrew their request. The dates are free again." + _DETAILS_TEXT,
        "<p>Hi {{ name }},</p><p><strong>{{ guest_name }}</strong> withdrew their request. The dates are free again.</p>" + _DETAILS_HTML,
    ),
    "booking_expired_guest": (
        "Your request expired: {{ title }}",
        "Hi {{ name }},\n\nThe host did not reply in time, so your request expired." + _DETAILS_TEXT
        + "\nYou were not charged. Try another place or other dates.\n",
        "<p>Hi {{ name }},</p><p>The host did not reply in time, so your request expired.</p>" + _DETAILS_HTML
        + "<p>You were not charged. Try another place or other dates.</p>",
    ),
    "booking_expired_host": (
        "A request expired: {{ title }}",
        "Hi {{ name }},\n\nYou did not respond to {{ guest_name }}'s request in time, so it expired and the dates were released."
        + _DETAILS_TEXT + "\nReplying quickly helps your listing get more bookings.\n",
        "<p>Hi {{ name }},</p><p>You did not respond to <strong>{{ guest_name }}</strong>'s request in time, so it expired "
        "and the dates were released.</p>" + _DETAILS_HTML + "<p>Replying quickly helps your listing get more bookings.</p>",
    ),
    "booking_payment_reminder_guest": (
        "Reminder: pay to keep your dates",
        "Hi {{ name }},\n\nYour accepted booking is waiting for payment." + _DETAILS_TEXT
        + "\nPay before {{ pay_by }} or the dates are released.\n\n{{ url }}/account/trips\n",
        "<p>Hi {{ name }},</p><p>Your accepted booking is waiting for payment.</p>" + _DETAILS_HTML
        + "<p>Pay before <strong>{{ pay_by }}</strong> or the dates are released.</p>" + _button("Go to my trip", "/account/trips"),
    ),
    "booking_payment_expired_guest": (
        "Your booking was released: {{ title }}",
        "Hi {{ name }},\n\nPayment was not completed in time, so the dates were released." + _DETAILS_TEXT + "\nYou were not charged.\n",
        "<p>Hi {{ name }},</p><p>Payment was not completed in time, so the dates were released.</p>" + _DETAILS_HTML
        + "<p>You were not charged.</p>",
    ),
    "booking_payment_expired_host": (
        "A booking was released: {{ title }}",
        "Hi {{ name }},\n\n{{ guest_name }} did not pay in time, so the dates were released." + _DETAILS_TEXT,
        "<p>Hi {{ name }},</p><p><strong>{{ guest_name }}</strong> did not pay in time, so the dates were released.</p>" + _DETAILS_HTML,
    ),
}
