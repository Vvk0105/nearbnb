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
    "payment_confirmed_guest": (
        "Booking confirmed: {{ title }}",
        "Hi {{ name }},\n\nYour payment of {{ total }} was received and your stay is confirmed." + _DETAILS_TEXT
        + "\nYour host: {{ host_name }}{% if host_phone %} ({{ host_phone }}){% endif %}, {{ host_email }}\n"
        "{% if checkin_instructions %}\nCheck-in instructions:\n{{ checkin_instructions }}\n{% endif %}"
        "\nYour receipt and tax invoice: {{ url }}/account/trips/{{ booking_id }}/invoice\n",
        "<p>Hi {{ name }},</p><p>Your payment of <strong>{{ total }}</strong> was received and your stay is confirmed.</p>" + _DETAILS_HTML
        + "<p>Your host: <strong>{{ host_name }}</strong>{% if host_phone %} ({{ host_phone }}){% endif %}, {{ host_email }}</p>"
        "{% if checkin_instructions %}<p><strong>Check-in instructions</strong><br>{{ checkin_instructions }}</p>{% endif %}"
        + _button("View receipt and tax invoice", "/account/trips/{{ booking_id }}/invoice"),
    ),
    "booking_confirmed_host": (
        "Booking confirmed: {{ title }}",
        "Hi {{ name }},\n\n{{ guest_name }} has paid and the booking is confirmed." + _DETAILS_TEXT
        + "Guest contact: {{ guest_email }}{% if guest_phone %}, {{ guest_phone }}{% endif %}\n"
        "You will receive {{ host_payable }} after the stay, once the payout delay has passed.\n\n{{ url }}/host/requests\n",
        "<p>Hi {{ name }},</p><p><strong>{{ guest_name }}</strong> has paid and the booking is confirmed.</p>" + _DETAILS_HTML
        + "<p>Guest contact: {{ guest_email }}{% if guest_phone %}, {{ guest_phone }}{% endif %}</p>"
        "<p>You will receive <strong>{{ host_payable }}</strong> after the stay, once the payout delay has passed.</p>"
        + _button("Open my bookings", "/host/requests"),
    ),
    "payment_failed_guest": (
        "Your payment did not go through: {{ title }}",
        "Hi {{ name }},\n\nYour card payment was not successful." + _DETAILS_TEXT
        + "\nYou can try again with another card until {{ pay_by }}: {{ url }}/account/trips/{{ booking_id }}/pay\n",
        "<p>Hi {{ name }},</p><p>Your card payment was not successful.</p>" + _DETAILS_HTML
        + "<p>You can try again with another card until <strong>{{ pay_by }}</strong>.</p>"
        + _button("Try again", "/account/trips/{{ booking_id }}/pay"),
    ),
    "payment_late_refund_guest": (
        "Your payment was refunded: {{ title }}",
        "Hi {{ name }},\n\nYour payment arrived after the dates had been released, so we refunded it in full ({{ total }})." + _DETAILS_TEXT
        + "\nThe refund can take 5 to 10 days to appear on your statement.\n",
        "<p>Hi {{ name }},</p><p>Your payment arrived after the dates had been released, so we refunded it in full (<strong>{{ total }}</strong>).</p>"
        + _DETAILS_HTML + "<p>The refund can take 5 to 10 days to appear on your statement.</p>",
    ),
    "booking_cancelled_guest": (
        "Booking cancelled: {{ title }}",
        "Hi {{ name }},\n\n{% if cancelled_by == 'guest' %}Your booking was cancelled.{% elif cancelled_by == 'host' %}Sorry, the host had to cancel your booking.{% else %}Your booking was cancelled by NearBNB.{% endif %}"
        + _DETAILS_TEXT + "{% if reason %}\nReason: {{ reason }}\n{% endif %}"
        "\n{% if refund_amount > 0 %}Refund: {{ refund }}{% if cancelled_by == 'guest' %} ({{ refund_pct }}% under the {{ policy_name }} cancellation policy; the service fee is not refundable unless stated){% endif %}. "
        "It can take 5 to 10 days to appear on your statement.{% else %}No refund is due under the {{ policy_name }} cancellation policy.{% endif %}\n",
        "<p>Hi {{ name }},</p><p>{% if cancelled_by == 'guest' %}Your booking was cancelled.{% elif cancelled_by == 'host' %}Sorry, the host had to cancel your booking.{% else %}Your booking was cancelled by NearBNB.{% endif %}</p>"
        + _DETAILS_HTML + "{% if reason %}<p><strong>Reason:</strong> {{ reason }}</p>{% endif %}"
        "{% if refund_amount > 0 %}<p>Refund: <strong>{{ refund }}</strong>{% if cancelled_by == 'guest' %} ({{ refund_pct }}% under the {{ policy_name }} cancellation policy){% endif %}. "
        "It can take 5 to 10 days to appear on your statement.</p>{% else %}<p>No refund is due under the {{ policy_name }} cancellation policy.</p>{% endif %}",
    ),
    "booking_cancelled_host": (
        "Booking cancelled: {{ title }}",
        "Hi {{ name }},\n\n{% if cancelled_by == 'guest' %}{{ guest_name }} cancelled their booking.{% elif cancelled_by == 'host' %}You cancelled this booking. The guest was refunded in full.{% else %}NearBNB cancelled this booking.{% endif %}"
        + _DETAILS_TEXT + "{% if reason %}\nReason: {{ reason }}\n{% endif %}"
        "\n{% if cancelled_by == 'host' %}No payout is due for this booking.{% else %}Your payout for this booking: {{ host_payout }}.{% endif %} The dates are open for new bookings again.\n",
        "<p>Hi {{ name }},</p><p>{% if cancelled_by == 'guest' %}<strong>{{ guest_name }}</strong> cancelled their booking.{% elif cancelled_by == 'host' %}You cancelled this booking. The guest was refunded in full.{% else %}NearBNB cancelled this booking.{% endif %}</p>"
        + _DETAILS_HTML + "{% if reason %}<p><strong>Reason:</strong> {{ reason }}</p>{% endif %}"
        "<p>{% if cancelled_by == 'host' %}No payout is due for this booking.{% else %}Your payout for this booking: <strong>{{ host_payout }}</strong>.{% endif %} The dates are open for new bookings again.</p>",
    ),
    "refund_requested_admin": (
        "Refund request to review: {{ reference }}",
        "A host asked for a refund.\n\nBooking: {{ reference }}\nHost: {{ host_name }}\nAmount: {{ amount }}\nReason: {{ reason }}\n\nReview it: {{ url }}/admin/refunds\n",
        "<p>A host asked for a refund.</p><p>Booking: <strong>{{ reference }}</strong><br>Host: {{ host_name }}<br>Amount: {{ amount }}<br>Reason: {{ reason }}</p>"
        + _button("Review the request", "/admin/refunds"),
    ),
    "refund_request_decided_host": (
        "Your refund request was {{ decision }}: {{ reference }}",
        "Hi {{ name }},\n\nYour request to refund {{ amount }} on booking {{ reference }} was {{ decision }}.{% if decision_note %}\nNote from NearBNB: {{ decision_note }}{% endif %}"
        "{% if decision == 'approved' %}\nThe amount is taken from your earnings for this booking.{% endif %}\n",
        "<p>Hi {{ name }},</p><p>Your request to refund <strong>{{ amount }}</strong> on booking {{ reference }} was <strong>{{ decision }}</strong>.</p>"
        "{% if decision_note %}<p>Note from NearBNB: {{ decision_note }}</p>{% endif %}"
        "{% if decision == 'approved' %}<p>The amount is taken from your earnings for this booking.</p>{% endif %}",
    ),
    "refund_issued_guest": (
        "You have been refunded {{ refund }}: {{ reference }}",
        "Hi {{ name }},\n\nWe refunded {{ refund }} to your card for booking {{ reference }}.\nReason: {{ reason }}\nIt can take 5 to 10 days to appear on your statement.\n",
        "<p>Hi {{ name }},</p><p>We refunded <strong>{{ refund }}</strong> to your card for booking {{ reference }}.</p><p>Reason: {{ reason }}</p>"
        "<p>It can take 5 to 10 days to appear on your statement.</p>",
    ),
    "payout_sent_host": (
        "Payout sent: {{ payout }} for {{ reference }}",
        "Hi {{ name }},\n\nWe sent {{ payout }} for booking {{ reference }} to your Stripe account. Stripe pays it into your bank on its own schedule.\n",
        "<p>Hi {{ name }},</p><p>We sent <strong>{{ payout }}</strong> for booking {{ reference }} to your Stripe account. Stripe pays it into your bank on its own schedule.</p>",
    ),
    "payout_failed_admin": (
        "Payout keeps failing: {{ reference }}",
        "The payout for booking {{ reference }} ({{ amount }}) failed repeatedly.\nError: {{ error }}\n\nCheck it in Admin > Bookings: {{ url }}/admin/bookings\n",
        "<p>The payout for booking <strong>{{ reference }}</strong> ({{ amount }}) failed repeatedly.</p><p>Error: {{ error }}</p>" + _button("Open bookings", "/admin/bookings"),
    ),
    "host_cancellation_alert_admin": (
        "Host cancellation alert: {{ host_name }}",
        "{{ host_name }} ({{ host_email }}) has cancelled {{ count }} confirmed bookings in the last {{ days }} days.\n\nReview the host: {{ url }}/admin/hosts/{{ host_id }}\n",
        "<p><strong>{{ host_name }}</strong> ({{ host_email }}) has cancelled {{ count }} confirmed bookings in the last {{ days }} days.</p>" + _button("Review the host", "/admin/hosts/{{ host_id }}"),
    ),
}
