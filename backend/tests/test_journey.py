"""One whole life of a booking, start to finish, through the public API (a backend stand-in for browser E2E)."""
from datetime import timedelta

from app.modules.bookings.models import Booking
from tests.conftest import csrf_headers
from tests.helpers import API
from tests.test_bookings import emails
from tests.test_cancellations import get, paid_world, shift, spy  # noqa: F401
from tests.test_payouts import release, transfers
from tests.test_payments import row
from tests.test_reviews import finish_stay, rate


async def test_a_booking_goes_from_request_to_payout_to_published_reviews(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)                      # request, host accepts, guest pays, confirmed
    guest_view = await get(w.guest, f"/me/bookings/{w.id}")
    assert guest_view["status"] == "confirmed" and guest_view["invoice_number"] == "INV-000001"
    assert len(await emails(db, "payment_confirmed_guest")) == 1 and len(await emails(db, "booking_confirmed_host")) == 1

    # The property is findable and the host sees the booking and the money coming.
    anon = await new_client()
    page = (await anon.get(f"{API}/properties/{w.prop['id']}")).json()
    assert page["id"] == w.prop["id"]
    host_view = await get(w.host, f"/host/bookings/{w.id}")
    assert host_view["payout"]["state"] == "scheduled"

    # The stay happens, the host is paid exactly once, the booking completes.
    await shift(db, w.id, -30)
    assert (await release())["paid"] == 1 and len(transfers()) == 1
    await finish_stay(db, w, hours_ago=60)
    assert await release() == {"paid": 0, "failed": 0, "skipped": 0}

    # Blind reviews: nothing public until both are in, then both publish together.
    assert (await rate(w.guest, "guest", w.id, 5, "Great stay")).status_code == 201
    assert (await get(anon, f"/properties/{w.prop['id']}/reviews"))["items"] == []
    assert (await rate(w.host, "host", w.id, 4, "Tidy guest")).status_code == 201
    public = await get(anon, f"/properties/{w.prop['id']}/reviews")
    assert [r["rating"] for r in public["items"]] == [5]
    assert "Tidy guest" not in str(public)

    # Admin sees it in the books, and the export is written to the audit trail.
    r = await w.admin.get(f"{API}/admin/exports/bookings.csv")
    assert r.status_code == 200 and w.b["reference"] in r.text
    detail = await get(w.admin, f"/admin/bookings/{w.id}")
    statuses = [e["to"] for e in detail["timeline"]]
    assert statuses[-1] == "completed" and "accepted" in statuses and "confirmed" in statuses
