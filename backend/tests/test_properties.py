import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.exc import IntegrityError

from app.modules.audit.models import AuditLog
from app.modules.properties.models import Room, RoomOccupancy
from tests.conftest import csrf_headers
from tests.helpers import (
    API, active_host, add_photos, admin_client, approved_host_without_stripe, build_listing, live_listing, png_bytes,
)


async def setup_world(new_client, make_admin):
    admin = await admin_client(new_client, make_admin)
    host, host_id = await active_host(new_client, admin)
    return admin, host, host_id


def future(days: int, hour: int = 14) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).strftime(f"%Y-%m-%dT{hour:02d}:00")


async def test_approved_host_can_draft_but_not_submit_until_active(new_client, make_admin):
    admin = await admin_client(new_client, make_admin)
    host, _ = await approved_host_without_stripe(new_client, admin)
    prop = await build_listing(host)
    assert prop["status"] == "draft" and prop["checklist"] == []
    r = await host.post(f"{API}/host/properties/{prop['id']}/submit", headers=csrf_headers(host))
    assert r.status_code == 403 and r.json()["error"]["code"] == "HOST_NOT_ACTIVE"


async def test_checklist_blocks_submission_until_complete(new_client, make_admin):
    _, host, _ = await setup_world(new_client, make_admin)
    h = csrf_headers(host)
    prop = (await host.post(f"{API}/host/properties", json={"name": "Bare"}, headers=h)).json()
    items = {c["item"] for c in prop["checklist"]}
    assert {"description", "address_line1", "type_id", "photos", "units"} <= items
    r = await host.post(f"{API}/host/properties/{prop['id']}/submit", headers=h)
    assert r.status_code == 422 and r.json()["error"]["code"] == "LISTING_INCOMPLETE"


async def test_submit_locks_editing_and_admin_approval_makes_it_public(new_client, make_admin):
    admin, host, _ = await setup_world(new_client, make_admin)
    h = csrf_headers(host)
    prop = await build_listing(host)
    anon = await new_client()
    assert (await anon.get(f"{API}/properties/{prop['id']}")).status_code == 404  # not public yet

    submitted = (await host.post(f"{API}/host/properties/{prop['id']}/submit", headers=h)).json()
    assert submitted["status"] == "submitted" and submitted["units"][0]["status"] == "submitted"
    locked = await host.patch(f"{API}/host/properties/{prop['id']}", json={"name": "Renamed"}, headers=h)
    assert locked.status_code == 409 and locked.json()["error"]["code"] == "UNDER_REVIEW"

    ah = csrf_headers(admin)
    r = await admin.post(f"{API}/admin/properties/{prop['id']}/decision", json={"decision": "reject"}, headers=ah)
    assert r.json()["error"]["code"] == "NOTE_REQUIRED"
    approved = (await admin.post(f"{API}/admin/properties/{prop['id']}/decision", json={"decision": "approve"}, headers=ah)).json()
    assert approved["status"] == "approved" and approved["units"][0]["status"] == "approved"

    public = (await anon.get(f"{API}/properties/{prop['id']}")).json()
    assert public["name"] == "Beach House" and len(public["units"]) == 1
    # privacy: no exact address, no host-only fields
    for hidden in ("address_line1", "address_line2", "postcode", "checkin_instructions", "review_note", "host_id", "checklist"):
        assert hidden not in public, hidden
    assert public["host_first_name"] == "Hannah" or public["host_first_name"]


async def test_changes_requested_lets_host_fix_and_resubmit(new_client, make_admin):
    admin, host, _ = await setup_world(new_client, make_admin)
    h = csrf_headers(host)
    prop = await build_listing(host)
    await host.post(f"{API}/host/properties/{prop['id']}/submit", headers=h)
    r = await admin.post(f"{API}/admin/properties/{prop['id']}/decision",
                         json={"decision": "request_changes", "note": "Photos are too dark"}, headers=csrf_headers(admin))
    assert r.json()["status"] == "changes_requested"
    mine = (await host.get(f"{API}/host/properties/{prop['id']}")).json()
    assert mine["review_note"] == "Photos are too dark"
    assert (await host.patch(f"{API}/host/properties/{prop['id']}", json={"name": "Brighter Beach House"}, headers=h)).status_code == 200
    assert (await host.post(f"{API}/host/properties/{prop['id']}/submit", headers=h)).json()["status"] == "submitted"


async def test_hosts_cannot_touch_each_others_listings(new_client, make_admin):
    admin, host, _ = await setup_world(new_client, make_admin)
    other, _ = await active_host(new_client, admin, "other@example.com")
    prop = await build_listing(host)
    unit_id = prop["units"][0]["id"]
    oh = csrf_headers(other)
    assert (await other.get(f"{API}/host/properties/{prop['id']}")).status_code == 404
    assert (await other.patch(f"{API}/host/properties/{prop['id']}", json={"name": "Mine now"}, headers=oh)).status_code == 404
    assert (await other.patch(f"{API}/host/units/{unit_id}", json={"price_per_24h": 1}, headers=oh)).status_code == 404
    assert (await other.post(f"{API}/host/units/{unit_id}/blocks", json={"start_local": future(5), "end_local": future(6)}, headers=oh)).status_code == 404
    assert (await other.get(f"{API}/host/properties")).json() == []


async def test_non_hosts_and_guests_cannot_use_host_or_admin_endpoints(new_client, make_admin):
    from tests.conftest import register_and_verify

    guest = await new_client()
    await register_and_verify(guest, "plain@example.com")
    assert (await guest.get(f"{API}/host/properties")).status_code == 404
    assert (await guest.get(f"{API}/admin/properties")).status_code == 403
    assert (await guest.get(f"{API}/admin/hosts")).status_code == 403
    assert (await guest.post(f"{API}/admin/catalogues", json={})).status_code in (403, 405)


async def test_sensitive_edits_go_to_review_and_the_live_version_stays(new_client, make_admin):
    admin, host, _ = await setup_world(new_client, make_admin)
    h, ah = csrf_headers(host), csrf_headers(admin)
    prop = await live_listing(host, admin)

    r = await host.patch(f"{API}/host/properties/{prop['id']}", json={"description": "Now with a hot tub!", "rules": "No parties"}, headers=h)
    body = r.json()
    assert r.status_code == 200
    assert body["result"]["pending_review"] == ["description"] and "rules" in body["result"]["applied"]  # description is sensitive by default, rules is not
    assert body["description"] == "A lovely place by the sea."  # live text unchanged
    assert body["pending_revisions"][0]["fields"] == ["description"]
    anon = await new_client()
    assert (await anon.get(f"{API}/properties/{prop['id']}")).json()["description"] == "A lovely place by the sea."

    queue = (await admin.get(f"{API}/admin/revisions")).json()
    assert len(queue) == 1 and queue[0]["fields"] == ["description"]
    r = await admin.post(f"{API}/admin/revisions/{queue[0]['id']}/decision", json={"decision": "approve"}, headers=ah)
    assert r.status_code == 200
    assert (await anon.get(f"{API}/properties/{prop['id']}")).json()["description"] == "Now with a hot tub!"
    assert (await admin.get(f"{API}/admin/revisions")).json() == []


async def test_saving_without_changing_anything_does_not_trigger_review(new_client, make_admin):
    """The editor sends every field on save; untouched fields must not count as edits."""
    admin, host, _ = await setup_world(new_client, make_admin)
    h = csrf_headers(host)
    prop = await live_listing(host, admin)
    unit = prop["units"][0]

    same = {"name": prop["name"], "description": prop["description"], "rules": prop["rules"], "type_id": prop["type_id"],
            "address_line1": prop["address_line1"], "suburb": prop["suburb"], "state": prop["state"], "postcode": prop["postcode"],
            "country": prop["country"], "timezone": prop["timezone"]}
    r = await host.patch(f"{API}/host/properties/{prop['id']}", json=same, headers=h)
    assert r.json()["result"] == {"applied": [], "pending_review": []}
    assert r.json()["pending_revisions"] == []

    # change only the rules (not sensitive) while re-sending the unchanged sensitive description
    r = await host.patch(f"{API}/host/properties/{prop['id']}", json={**same, "rules": "No smoking"}, headers=h)
    assert r.json()["result"] == {"applied": ["rules"], "pending_review": []}

    unit_same = {"name": unit["name"], "description": unit["description"], "price_per_24h": unit["price_per_24h"],
                 "cleaning_fee": unit["cleaning_fee"], "capacity": unit["capacity"], "quantity": unit["quantity"],
                 "checkin_from": unit["checkin_from"], "checkin_to": unit["checkin_to"],
                 "amenity_ids": [a["id"] for a in unit["amenities"]]}
    await admin.put(f"{API}/admin/settings/listing.reapproval_fields",
                    json={"value": ["address", "property_type", "photos", "description", "price", "capacity", "amenities"]},
                    headers=csrf_headers(admin))
    r = await host.patch(f"{API}/host/units/{unit['id']}", json=unit_same, headers=h)
    assert r.json()["result"] == {"applied": [], "pending_review": []}
    assert (await admin.get(f"{API}/admin/revisions")).json() == []

    # a real change among the same fields is still queued
    r = await host.patch(f"{API}/host/units/{unit['id']}", json={**unit_same, "price_per_24h": 40000}, headers=h)
    assert r.json()["result"]["pending_review"] == ["price_per_24h"]


async def test_rejected_revision_changes_nothing(new_client, make_admin):
    admin, host, _ = await setup_world(new_client, make_admin)
    prop = await live_listing(host, admin)
    await host.patch(f"{API}/host/properties/{prop['id']}", json={"address_line1": "99 Other St"}, headers=csrf_headers(host))
    rev = (await admin.get(f"{API}/admin/revisions")).json()[0]
    ah = csrf_headers(admin)
    assert (await admin.post(f"{API}/admin/revisions/{rev['id']}/decision", json={"decision": "reject"}, headers=ah)).json()["error"]["code"] == "NOTE_REQUIRED"
    assert (await admin.post(f"{API}/admin/revisions/{rev['id']}/decision", json={"decision": "reject", "note": "Address mismatch"}, headers=ah)).status_code == 200
    assert (await host.get(f"{API}/host/properties/{prop['id']}")).json()["address_line1"] == "1 Beach Rd"
    again = await admin.post(f"{API}/admin/revisions/{rev['id']}/decision", json={"decision": "approve"}, headers=ah)
    assert again.status_code == 409


async def test_admin_can_change_which_edits_need_reapproval(new_client, make_admin):
    admin, host, _ = await setup_world(new_client, make_admin)
    h = csrf_headers(host)
    prop = await live_listing(host, admin)
    unit_id = prop["units"][0]["id"]

    # default: price changes apply immediately
    r = await host.patch(f"{API}/host/units/{unit_id}", json={"price_per_24h": 30000}, headers=h)
    assert r.json()["result"] == {"applied": ["price_per_24h"], "pending_review": []}
    assert r.json()["units"][0]["price_per_24h"] == 30000

    # admin switches price (and capacity) on
    r = await admin.put(f"{API}/admin/settings/listing.reapproval_fields",
                        json={"value": ["address", "property_type", "photos", "description", "price", "capacity"]}, headers=csrf_headers(admin))
    assert r.status_code == 200
    r = await host.patch(f"{API}/host/units/{unit_id}", json={"price_per_24h": 99900, "capacity": 6}, headers=h)
    assert r.json()["result"]["pending_review"] == ["capacity", "price_per_24h"]
    assert r.json()["units"][0]["price_per_24h"] == 30000 and r.json()["units"][0]["capacity"] == 4

    rev = (await admin.get(f"{API}/admin/revisions")).json()[0]
    await admin.post(f"{API}/admin/revisions/{rev['id']}/decision", json={"decision": "approve"}, headers=csrf_headers(admin))
    unit = (await host.get(f"{API}/host/properties/{prop['id']}")).json()["units"][0]
    assert unit["price_per_24h"] == 99900 and unit["capacity"] == 6


async def test_photo_changes_on_a_live_listing_wait_for_approval(new_client, make_admin):
    admin, host, _ = await setup_world(new_client, make_admin)
    h = csrf_headers(host)
    prop = await live_listing(host, admin)
    anon = await new_client()
    before = (await anon.get(f"{API}/properties/{prop['id']}")).json()["photos"]
    assert len(before) == 3

    added = await add_photos(host, f"{API}/host/properties/{prop['id']}/photos", 1)
    statuses = sorted(p["status"] for p in added["photos"])
    assert statuses == ["approved", "approved", "approved", "pending_add"]
    assert len((await anon.get(f"{API}/properties/{prop['id']}")).json()["photos"]) == 3  # new one hidden

    first = added["photos"][0]["id"]
    r = await host.delete(f"{API}/host/properties/{prop['id']}/photos/{first}", headers=h)
    assert any(p["status"] == "pending_delete" for p in r.json()["photos"])
    assert len((await anon.get(f"{API}/properties/{prop['id']}")).json()["photos"]) == 3  # still shown until approved

    rev = (await admin.get(f"{API}/admin/revisions")).json()[0]
    assert rev["photos_changed"] is True
    await admin.post(f"{API}/admin/revisions/{rev['id']}/decision", json={"decision": "approve"}, headers=csrf_headers(admin))
    public = (await anon.get(f"{API}/properties/{prop['id']}")).json()["photos"]
    assert len(public) == 3 and first not in {p["id"] for p in public}


async def test_photo_upload_rejects_non_images_and_enforces_the_limit(new_client, make_admin):
    admin, host, _ = await setup_world(new_client, make_admin)
    h = csrf_headers(host)
    prop = (await host.post(f"{API}/host/properties", json={"name": "Photos"}, headers=h)).json()
    url = f"{API}/host/properties/{prop['id']}/photos"
    bad = await host.post(url, files={"file": ("x.png", b"definitely not an image", "image/png")}, headers=h)
    assert bad.status_code == 415
    await admin.put(f"{API}/admin/settings/listing.max_photos", json={"value": 2}, headers=csrf_headers(admin))
    await add_photos(host, url, 2)
    over = await host.post(url, files={"file": ("x.png", png_bytes(), "image/png")}, headers=h)
    assert over.status_code == 422 and over.json()["error"]["code"] == "TOO_MANY_PHOTOS"


async def test_quantity_creates_rooms_and_reducing_it_is_safe(new_client, make_admin, db):
    admin, host, _ = await setup_world(new_client, make_admin)
    h = csrf_headers(host)
    prop = await build_listing(host, quantity=4, name="Grand Hotel")
    unit = prop["units"][0]
    assert [r["label"] for r in unit["rooms"]] == ["1", "2", "3", "4"]

    r = await host.patch(f"{API}/host/units/{unit['id']}", json={"quantity": 6}, headers=h)
    assert len(r.json()["units"][0]["rooms"]) == 6
    r = await host.patch(f"{API}/host/units/{unit['id']}", json={"quantity": 2}, headers=h)
    assert len(r.json()["units"][0]["rooms"]) == 2

    # a room with an upcoming booking cannot be removed
    rooms = (await db.execute(select(Room).where(Room.unit_id == uuid.UUID(unit["id"]), Room.active.is_(True)))).scalars().all()
    now = datetime.now(UTC)
    for room in rooms:
        db.add(RoomOccupancy(room_id=room.id, period=Range(now + timedelta(days=3), now + timedelta(days=4), bounds="[)"), kind="booking"))
    await db.commit()
    r = await host.patch(f"{API}/host/units/{unit['id']}", json={"quantity": 1}, headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "ROOMS_IN_USE"

    renamed = await host.patch(f"{API}/host/units/{unit['id']}/rooms/{rooms[0].id}", json={"label": "Penthouse"}, headers=h)
    assert renamed.status_code == 200
    dup = await host.patch(f"{API}/host/units/{unit['id']}/rooms/{rooms[1].id}", json={"label": "Penthouse"}, headers=h)
    assert dup.status_code == 409


async def test_duplicate_unit_and_checkin_window_validation(new_client, make_admin):
    _, host, _ = await setup_world(new_client, make_admin)
    h = csrf_headers(host)
    prop = await build_listing(host)
    unit = prop["units"][0]
    r = await host.post(f"{API}/host/units/{unit['id']}/duplicate", headers=h)
    names = [u["name"] for u in r.json()["units"]]
    assert "Entire house (copy)" in names and len(names) == 2
    bad = await host.patch(f"{API}/host/units/{unit['id']}", json={"checkin_from": "22:00:00", "checkin_to": "08:00:00"}, headers=h)
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "INVALID_CHECKIN_WINDOW"
    bad = await host.patch(f"{API}/host/units/{unit['id']}", json={"max_stay_hours": 30}, headers=h)
    assert bad.status_code == 422


async def test_pause_resume_unpublish_and_archive(new_client, make_admin):
    admin, host, _ = await setup_world(new_client, make_admin)
    h, ah = csrf_headers(host), csrf_headers(admin)
    prop = await live_listing(host, admin)
    anon = await new_client()
    assert (await anon.get(f"{API}/properties/{prop['id']}")).status_code == 200

    assert (await host.post(f"{API}/host/properties/{prop['id']}/pause", headers=h)).json()["status"] == "paused"
    assert (await anon.get(f"{API}/properties/{prop['id']}")).status_code == 404
    assert (await host.post(f"{API}/host/properties/{prop['id']}/resume", headers=h)).json()["status"] == "approved"

    r = await admin.post(f"{API}/admin/properties/{prop['id']}/unpublish", json={}, headers=ah)
    assert r.json()["error"]["code"] == "REASON_REQUIRED"
    assert (await admin.post(f"{API}/admin/properties/{prop['id']}/unpublish", json={"reason": "Policy breach"}, headers=ah)).json()["status"] == "unpublished"
    assert (await anon.get(f"{API}/properties/{prop['id']}")).status_code == 404
    assert (await host.post(f"{API}/host/properties/{prop['id']}/resume", headers=h)).status_code == 409  # host cannot undo an admin action
    assert (await admin.post(f"{API}/admin/properties/{prop['id']}/republish", headers=ah)).json()["status"] == "approved"

    assert (await host.post(f"{API}/host/properties/{prop['id']}/archive", headers=h)).json()["status"] == "archived"
    assert (await host.get(f"{API}/host/properties")).json() == []


async def test_suspended_host_listings_disappear_and_edits_stop(new_client, make_admin):
    admin, host, host_id = await setup_world(new_client, make_admin)
    prop = await live_listing(host, admin)
    anon = await new_client()
    assert (await anon.get(f"{API}/properties/{prop['id']}")).status_code == 200
    await admin.post(f"{API}/admin/hosts/{host_id}/suspend", json={"reason": "Investigation"}, headers=csrf_headers(admin))
    assert (await anon.get(f"{API}/properties/{prop['id']}")).status_code == 404
    r = await host.patch(f"{API}/host/properties/{prop['id']}", json={"rules": "x"}, headers=csrf_headers(host))
    assert r.status_code == 403 and r.json()["error"]["code"] == "HOST_SUSPENDED"


async def test_new_unit_on_a_live_property_needs_its_own_approval(new_client, make_admin):
    admin, host, _ = await setup_world(new_client, make_admin)
    h = csrf_headers(host)
    prop = await live_listing(host, admin)
    r = await host.post(f"{API}/host/properties/{prop['id']}/units", json={"name": "Pool villa", "price_per_24h": 50000, "capacity": 2}, headers=h)
    new_unit = next(u for u in r.json()["units"] if u["name"] == "Pool villa")
    assert new_unit["status"] == "draft"
    anon = await new_client()
    assert [u["name"] for u in (await anon.get(f"{API}/properties/{prop['id']}")).json()["units"]] == ["Entire house"]

    incomplete = await host.post(f"{API}/host/units/{new_unit['id']}/submit", headers=h)
    assert incomplete.json()["error"]["code"] == "LISTING_INCOMPLETE"  # needs a photo
    await add_photos(host, f"{API}/host/units/{new_unit['id']}/photos", 1)
    assert (await host.post(f"{API}/host/units/{new_unit['id']}/submit", headers=h)).status_code == 200
    d = await admin.post(f"{API}/admin/units/{new_unit['id']}/decision", json={"decision": "approve"}, headers=csrf_headers(admin))
    assert d.status_code == 200
    assert sorted(u["name"] for u in (await anon.get(f"{API}/properties/{prop['id']}")).json()["units"]) == ["Entire house", "Pool villa"]


# ---------- availability blocks ----------

async def test_host_blocks_show_on_calendar_and_cannot_overlap(new_client, make_admin):
    admin, host, _ = await setup_world(new_client, make_admin)
    h = csrf_headers(host)
    prop = await build_listing(host)
    unit_id = prop["units"][0]["id"]

    r = await host.post(f"{API}/host/units/{unit_id}/blocks", json={"start_local": future(10), "end_local": future(12), "note": "Maintenance"}, headers=h)
    assert r.status_code == 201
    block = r.json()["blocks"][0]
    assert block["kind"] == "host_block" and block["note"] == "Maintenance"

    overlap = await host.post(f"{API}/host/units/{unit_id}/blocks", json={"start_local": future(11), "end_local": future(13)}, headers=h)
    assert overlap.status_code == 409 and overlap.json()["error"]["code"] == "SLOT_UNAVAILABLE"
    assert "existing block" in overlap.json()["error"]["message"]
    adjacent = await host.post(f"{API}/host/units/{unit_id}/blocks", json={"start_local": future(12), "end_local": future(14)}, headers=h)
    assert adjacent.status_code == 201  # back-to-back is fine (end is exclusive)

    day = lambda n: (datetime.now(UTC) + timedelta(days=n)).strftime("%Y-%m-%d")
    cal = (await host.get(f"{API}/host/units/{unit_id}/calendar", params={"from": day(9), "to": day(20)})).json()
    assert len(cal["entries"]) == 2 and cal["timezone"] == "Australia/Sydney"
    assert (await host.delete(f"{API}/host/units/{unit_id}/blocks/{block['id']}", headers=h)).status_code == 200
    cal = (await host.get(f"{API}/host/units/{unit_id}/calendar", params={"from": day(9), "to": day(20)})).json()
    assert len(cal["entries"]) == 1


async def test_block_input_validation(new_client, make_admin):
    admin, host, _ = await setup_world(new_client, make_admin)
    h = csrf_headers(host)
    unit_id = (await build_listing(host))["units"][0]["id"]
    url = f"{API}/host/units/{unit_id}/blocks"
    cases = [
        ({"start_local": future(5), "end_local": future(4)}, "INVALID_RANGE"),
        ({"start_local": "not a date", "end_local": future(4)}, "INVALID_DATETIME"),
        ({"start_local": future(5) + "+10:00", "end_local": future(6)}, "INVALID_DATETIME"),
        ({"start_local": "2020-01-01T10:00", "end_local": "2020-01-02T10:00"}, "IN_THE_PAST"),
        ({"start_local": future(5), "end_local": future(900)}, "RANGE_TOO_LONG"),
    ]
    for body, code in cases:
        r = await host.post(url, json=body, headers=h)
        assert r.status_code == 422 and r.json()["error"]["code"] == code, (body, r.text)

    # The check-in step is an admin setting. Default is 60 minutes, so a half-hour start is refused...
    off_step = (datetime.now(UTC) + timedelta(days=5)).strftime("%Y-%m-%dT14:30")
    r = await host.post(url, json={"start_local": off_step, "end_local": future(6)}, headers=h)
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_TIME_STEP"
    # ...until the admin switches the interval to 30 minutes.
    await admin.put(f"{API}/admin/settings/checkin.interval_minutes", json={"value": 30}, headers=csrf_headers(admin))
    assert (await host.post(url, json={"start_local": off_step, "end_local": future(6)}, headers=h)).status_code == 201


async def test_block_spans_dst_change_correctly(new_client, make_admin, db):
    """Times are entered in the property's own timezone; Sydney moves clocks forward on the first Sunday of October."""
    admin, host, _ = await setup_world(new_client, make_admin)
    unit_id = (await build_listing(host))["units"][0]["id"]
    # Sunday 2027-10-03 02:00 Sydney clocks go to 03:00. 24 real hours from 2027-10-02 14:00 AEST ends 15:00 AEDT.
    r = await host.post(f"{API}/host/units/{unit_id}/blocks", json={"start_local": "2027-10-02T14:00", "end_local": "2027-10-03T15:00"}, headers=csrf_headers(host))
    assert r.status_code == 201
    block = r.json()["blocks"][0]
    start = datetime.fromisoformat(block["start"])
    end = datetime.fromisoformat(block["end"])
    assert start.astimezone(UTC).hour == 4          # 14:00 AEST (UTC+10) is 04:00 UTC
    assert (end - start) == timedelta(hours=24)     # 14:00 -> 15:00 next day is exactly 24 real hours


# ---------- the database itself refuses double booking ----------

async def test_database_rejects_overlapping_occupancy_even_when_the_api_is_bypassed(new_client, make_admin, db):
    _, host, _ = await setup_world(new_client, make_admin)
    unit = (await build_listing(host, quantity=2))["units"][0]
    room_a, room_b = [uuid.UUID(r["id"]) for r in unit["rooms"]]
    start = datetime.now(UTC) + timedelta(days=20)
    end = start + timedelta(hours=24)

    db.add(RoomOccupancy(room_id=room_a, period=Range(start, end, bounds="[)"), kind="booking"))
    await db.commit()

    db.add(RoomOccupancy(room_id=room_a, period=Range(start + timedelta(hours=5), end + timedelta(hours=5), bounds="[)"), kind="hold"))
    with pytest.raises(IntegrityError, match="room_occupancy_no_overlap"):
        await db.commit()
    await db.rollback()

    # same time on a different room is fine, and so is starting exactly when the first one ends
    db.add(RoomOccupancy(room_id=room_b, period=Range(start, end, bounds="[)"), kind="booking"))
    db.add(RoomOccupancy(room_id=room_a, period=Range(end, end + timedelta(hours=24), bounds="[)"), kind="booking"))
    await db.commit()


async def test_hotel_blocks_one_room_or_all_rooms(new_client, make_admin):
    _, host, _ = await setup_world(new_client, make_admin)
    h = csrf_headers(host)
    unit = (await build_listing(host, quantity=3, name="Hotel"))["units"][0]
    url = f"{API}/host/units/{unit['id']}/blocks"
    rooms = unit["rooms"]

    ambiguous = await host.post(url, json={"start_local": future(8), "end_local": future(9)}, headers=h)
    assert ambiguous.status_code == 422 and ambiguous.json()["error"]["code"] == "ROOM_REQUIRED"

    one = await host.post(url, json={"start_local": future(8), "end_local": future(9), "room_id": rooms[0]["id"]}, headers=h)
    assert len(one.json()["blocks"]) == 1

    # blocking all rooms fails as a whole when one room is already taken, and leaves nothing behind
    clash = await host.post(url, json={"start_local": future(8), "end_local": future(10), "all_rooms": True}, headers=h)
    assert clash.status_code == 409
    day = lambda n: (datetime.now(UTC) + timedelta(days=n)).strftime("%Y-%m-%d")
    cal = (await host.get(f"{API}/host/units/{unit['id']}/calendar", params={"from": day(7), "to": day(12)})).json()
    assert len(cal["entries"]) == 1

    everything = await host.post(url, json={"start_local": future(15), "end_local": future(16), "all_rooms": True}, headers=h)
    assert len(everything.json()["blocks"]) == 3


async def test_archive_is_refused_while_bookings_exist(new_client, make_admin, db):
    admin, host, _ = await setup_world(new_client, make_admin)
    prop = await live_listing(host, admin)
    room_id = uuid.UUID(prop["units"][0]["rooms"][0]["id"])
    now = datetime.now(UTC)
    occ = RoomOccupancy(room_id=room_id, period=Range(now + timedelta(days=2), now + timedelta(days=3), bounds="[)"), kind="booking")
    db.add(occ)
    await db.commit()
    r = await host.post(f"{API}/host/properties/{prop['id']}/archive", headers=csrf_headers(host))
    assert r.status_code == 409 and r.json()["error"]["code"] == "HAS_UPCOMING_BOOKINGS"
    await db.delete(occ)
    await db.commit()
    assert (await host.post(f"{API}/host/properties/{prop['id']}/archive", headers=csrf_headers(host))).status_code == 200


async def test_catalogue_management_is_audited_and_hides_inactive_items(new_client, make_admin, db):
    admin, host, _ = await setup_world(new_client, make_admin)
    ah = csrf_headers(admin)
    created = (await admin.post(f"{API}/admin/property-types", json={"name": "Treehouse"}, headers=ah)).json()
    dup = await admin.post(f"{API}/admin/property-types", json={"name": "Treehouse"}, headers=ah)
    assert dup.status_code == 409
    meta = (await host.get(f"{API}/meta/listing")).json()
    assert "Treehouse" in [t["name"] for t in meta["property_types"]]
    await admin.put(f"{API}/admin/property-types/{created['id']}", json={"name": "Treehouse", "active": False}, headers=ah)
    meta = (await host.get(f"{API}/meta/listing")).json()
    assert "Treehouse" not in [t["name"] for t in meta["property_types"]]
    r = await host.post(f"{API}/host/properties", json={"name": "T", "type_id": created["id"]}, headers=csrf_headers(host))
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_PROPERTY_TYPE"
    actions = (await db.execute(select(AuditLog.action).where(AuditLog.entity_type == "property_type"))).scalars().all()
    assert actions.count("property_type.created") == 1 and "property_type.updated" in actions


async def test_an_emptied_timezone_box_never_saves_an_empty_timezone(new_client, make_admin):
    """A host who clears the Timezone box used to save "" and every calendar of that place then failed with an error.
    An empty box now means "set it from the state"."""
    admin, host, _ = await setup_world(new_client, make_admin)
    h = csrf_headers(host)
    meta = (await host.get(f"{API}/meta/listing")).json()
    r = await host.post(f"{API}/host/properties", headers=h, json={
        "name": "Roadhouse", "type_id": meta["property_types"][0]["id"], "suburb": "Ghan", "state": "NT", "postcode": "0872", "timezone": "  ",
    })
    assert r.status_code == 201 and r.json()["timezone"] == "Australia/Darwin"

    prop = await live_listing(host, admin)
    r = await host.patch(f"{API}/host/properties/{prop['id']}", json={"timezone": ""}, headers=h)
    assert r.status_code == 200 and r.json()["timezone"] == "Australia/Sydney"     # unchanged, not emptied
    r = await host.patch(f"{API}/host/properties/{prop['id']}", json={"timezone": "Mars/Base"}, headers=h)
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_TIMEZONE"
    unit = prop["units"][0]
    r = await host.get(f"{API}/units/{unit['id']}/availability/calendar", params={"month": datetime.now(UTC).strftime("%Y-%m")})
    assert r.status_code == 200
