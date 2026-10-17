import uuid

from tests.conftest import csrf_headers
from tests.helpers import API, active_host, admin_client, build_listing, day_local, live_listing, occupy, set_location

SYDNEY = (-33.8688, 151.2093)
BONDI = (-33.8915, 151.2767)       # about 7 km from the Sydney CBD
MELBOURNE = (-37.8136, 144.9631)   # about 700 km away


async def search(anon, **params):
    r = await anon.get(f"{API}/search", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def names(result):
    return [i["name"] for i in result["items"]]


async def make_world(new_client, make_admin, n_hosts=1):
    admin = await admin_client(new_client, make_admin)
    hosts = []
    for i in range(n_hosts):
        host, host_id = await active_host(new_client, admin, f"host{i}@example.com")
        hosts.append((host, host_id))
    return admin, hosts, await new_client()


async def test_only_live_listings_by_active_hosts_appear(new_client, make_admin):
    admin, [(host, host_id)], anon = await make_world(new_client, make_admin)
    draft = await build_listing(host, name="Draft Villa")                 # never submitted
    live = await live_listing(host, admin, name="Live Cottage")
    assert names(await search(anon)) == ["Live Cottage"]

    h = csrf_headers(host)
    await host.post(f"{API}/host/properties/{live['id']}/pause", headers=h)
    assert (await search(anon))["total"] == 0
    await host.post(f"{API}/host/properties/{live['id']}/resume", headers=h)
    assert (await search(anon))["total"] == 1
    await admin.post(f"{API}/admin/hosts/{host_id}/suspend", json={"reason": "x"}, headers=csrf_headers(admin))
    assert (await search(anon))["total"] == 0
    assert draft["status"] == "draft"


async def test_result_cards_show_cover_price_and_no_private_details(new_client, make_admin):
    admin, [(host, _)], anon = await make_world(new_client, make_admin)
    prop = await live_listing(host, admin, name="Card Test")
    item = (await search(anon))["items"][0]
    assert item["name"] == "Card Test" and item["suburb"] == "Bondi" and item["min_price"] == 25000 and item["currency"] == "AUD"
    assert item["cover_url"].startswith("/api/v1/media/public/listings/property/") and item["thumb_url"].endswith("_thumb.webp")
    assert item["units"][0]["name"] == "Entire house"
    for private in ("address_line1", "postcode", "host_id", "latitude", "longitude", "description"):
        assert private not in item
    assert (await anon.get(item["cover_url"])).status_code == 200
    assert prop["id"] == item["id"]


async def test_text_search_matches_suburb_state_postcode_and_name_case_insensitively(new_client, make_admin):
    admin, [(host, _)], anon = await make_world(new_client, make_admin)
    await live_listing(host, admin, name="Surf Shack")
    for q in ("bondi", "BONDI", "Bondi NSW", "bondi, nsw", "2026", "surf", "Bon"):
        assert (await search(anon, q=q))["total"] == 1, q
    for q in ("Melbourne", "Bondi VIC", "9999"):
        assert (await search(anon, q=q))["total"] == 0, q
    assert (await search(anon, q="a"))["total"] == 1  # one-letter input is ignored, not matched everywhere


async def test_location_search_uses_distance_and_sorts_nearest_first(new_client, make_admin, db):
    admin, [(h1, _), (h2, _)], anon = await make_world(new_client, make_admin, n_hosts=2)
    near = await live_listing(h1, admin, name="Near Sydney")
    far = await live_listing(h2, admin, name="Far Melbourne")
    await set_location(db, near["id"], *BONDI)
    await set_location(db, far["id"], *MELBOURNE)

    r = await search(anon, lat=SYDNEY[0], lng=SYDNEY[1], radius_km=25)
    assert names(r) == ["Near Sydney"] and 5 < r["items"][0]["distance_km"] < 10
    r = await search(anon, lat=SYDNEY[0], lng=SYDNEY[1], radius_km=300)
    assert names(r) == ["Near Sydney"]                       # Melbourne is still too far
    r = await search(anon, lat=MELBOURNE[0], lng=MELBOURNE[1], radius_km=25)
    assert names(r) == ["Far Melbourne"]

    await set_location(db, far["id"], -33.80, 151.00)       # now also near Sydney, but further than Bondi
    r = await search(anon, lat=SYDNEY[0], lng=SYDNEY[1], radius_km=50)
    assert names(r) == ["Near Sydney", "Far Melbourne"]
    assert r["items"][0]["distance_km"] < r["items"][1]["distance_km"]


async def test_places_without_coordinates_still_match_by_name_during_a_location_search(new_client, make_admin, db):
    admin, [(h1, _), (h2, _)], anon = await make_world(new_client, make_admin, n_hosts=2)
    with_coords = await live_listing(h1, admin, name="Pinned")
    await live_listing(h2, admin, name="Unpinned")               # suburb Bondi, no latitude/longitude
    await set_location(db, with_coords["id"], *BONDI)
    both = await search(anon, lat=BONDI[0], lng=BONDI[1], q="Bondi")
    assert sorted(names(both)) == ["Pinned", "Unpinned"]
    assert [i["distance_km"] for i in both["items"] if i["name"] == "Unpinned"] == [None]
    only_pinned = await search(anon, lat=BONDI[0], lng=BONDI[1])   # no name to fall back on
    assert names(only_pinned) == ["Pinned"]


async def test_filters_guests_price_type_and_amenities(new_client, make_admin):
    admin, [(h1, _), (h2, _)], anon = await make_world(new_client, make_admin, n_hosts=2)
    cheap = await live_listing(h1, admin, name="Cheap")
    dear = await live_listing(h2, admin, name="Dear")
    await h2.patch(f"{API}/host/units/{dear['units'][0]['id']}", json={"price_per_24h": 60000, "capacity": 8}, headers=csrf_headers(h2))
    meta = (await anon.get(f"{API}/meta/listing")).json()

    assert sorted(names(await search(anon))) == ["Cheap", "Dear"]
    assert names(await search(anon, min_price=30000)) == ["Dear"]
    assert names(await search(anon, max_price=30000)) == ["Cheap"]
    assert names(await search(anon, guests=6)) == ["Dear"]       # capacity 8 vs 4
    assert (await search(anon, guests=9))["total"] == 0
    assert (await search(anon, min_price=26000, max_price=59999))["total"] == 0

    first_type, other_type = meta["property_types"][0]["id"], meta["property_types"][1]["id"]
    assert (await search(anon, type_ids=first_type))["total"] == 2
    assert (await search(anon, type_ids=other_type))["total"] == 0
    assert (await search(anon, type_ids=[other_type, first_type]))["total"] == 2

    wifi, pool = meta["amenities"][0]["id"], meta["amenities"][1]["id"]
    assert (await search(anon, amenity_ids=wifi))["total"] == 2
    assert (await search(anon, amenity_ids=[wifi, pool]))["total"] == 0   # must have BOTH
    assert cheap["id"] != dear["id"]


async def test_sorting_and_pagination(new_client, make_admin):
    admin, [(h1, _), (h2, _)], anon = await make_world(new_client, make_admin, n_hosts=2)
    await live_listing(h1, admin, name="First")
    second = await live_listing(h2, admin, name="Second")
    await h2.patch(f"{API}/host/units/{second['units'][0]['id']}", json={"price_per_24h": 10000}, headers=csrf_headers(h2))

    assert names(await search(anon, sort="price_asc")) == ["Second", "First"]
    assert names(await search(anon, sort="price_desc")) == ["First", "Second"]
    assert names(await search(anon, sort="newest")) == ["Second", "First"]
    p1 = await search(anon, sort="price_asc", page=1, page_size=1)
    p2 = await search(anon, sort="price_asc", page=2, page_size=1)
    assert (p1["total"], names(p1), names(p2)) == (2, ["Second"], ["First"])
    assert (await search(anon, page=3, page_size=1))["items"] == []


async def test_availability_filter_hides_places_that_are_taken(new_client, make_admin, db):
    admin, [(h1, _), (h2, _)], anon = await make_world(new_client, make_admin, n_hosts=2)
    a = await live_listing(h1, admin, name="Booked Place")
    await live_listing(h2, admin, name="Free Place")
    await occupy(db, a["units"][0]["rooms"][0]["id"], day_local(10, 14), day_local(11, 14))

    assert sorted(names(await search(anon, checkin=day_local(10)))) == ["Free Place"]
    assert sorted(names(await search(anon, checkin=day_local(12)))) == ["Booked Place", "Free Place"]
    assert sorted(names(await search(anon, checkin=day_local(11)))) == ["Booked Place", "Free Place"]   # back-to-back is fine
    assert names(await search(anon, checkin=day_local(9, 14), hours=48)) == ["Free Place"]             # a 2-night stay runs into it
    assert sorted(names(await search(anon, checkin=day_local(9, 14), hours=24))) == ["Booked Place", "Free Place"]


async def test_hotel_stays_listed_until_every_room_is_taken(new_client, make_admin, db):
    admin, [(host, _)], anon = await make_world(new_client, make_admin)
    hotel = await live_listing(host, admin, name="Grand Hotel", quantity=2)
    room_a, room_b = [r["id"] for r in hotel["units"][0]["rooms"]]
    await occupy(db, room_a, day_local(10, 14), day_local(11, 14))
    assert names(await search(anon, checkin=day_local(10))) == ["Grand Hotel"]
    await occupy(db, room_b, day_local(10, 14), day_local(11, 14))
    assert (await search(anon, checkin=day_local(10)))["total"] == 0


async def test_search_applies_each_hosts_booking_rules(new_client, make_admin):
    admin, [(h1, id1), (h2, _)], anon = await make_world(new_client, make_admin, n_hosts=2)
    await live_listing(h1, admin, name="Needs Notice")
    await live_listing(h2, admin, name="Flexible")
    await admin.put(f"{API}/admin/hosts/{id1}/overrides/booking.min_lead_time_hours", json={"value": 24 * 30}, headers=csrf_headers(admin))
    assert names(await search(anon, checkin=day_local(10))) == ["Flexible"]   # first host needs 30 days notice


async def test_search_explains_why_nothing_matched_when_the_time_is_not_allowed(new_client, make_admin):
    admin, [(host, _)], anon = await make_world(new_client, make_admin)
    await live_listing(host, admin)
    r = await search(anon, checkin=day_local(10, 3))      # 3 AM is outside the 8 AM - 10 PM check-in window
    assert r["total"] == 0 and "Check-in is only possible between" in r["hint"]
    r = await search(anon, checkin=day_local(10, 14))
    assert r["total"] == 1 and r["hint"] is None


async def test_search_input_validation(new_client, make_admin):
    anon = await new_client()
    for params, code in [({"checkin": "soon"}, "INVALID_DATETIME"), ({"hours": 36}, "INVALID_DURATION"), ({"lat": 10}, "INVALID_LOCATION")]:
        r = await anon.get(f"{API}/search", params=params)
        assert r.status_code == 422 and r.json()["error"]["code"] == code, params
    for params in ({"lat": 200, "lng": 0}, {"guests": 0}, {"radius_km": 0}, {"sort": "random"}, {"page_size": 500}, {"type_ids": "not-a-uuid"}):
        assert (await anon.get(f"{API}/search", params=params)).status_code == 422, params


async def test_search_cannot_be_used_to_inject_sql(new_client, make_admin):
    admin, [(host, _)], anon = await make_world(new_client, make_admin)
    await live_listing(host, admin)
    for q in ("'; DROP TABLE properties; --", "%", "_", "\\", "Bondi' OR '1'='1"):
        r = await anon.get(f"{API}/search", params={"q": q})
        assert r.status_code == 200
    assert (await search(anon))["total"] == 1   # table still there, data intact
    assert uuid.uuid4()
