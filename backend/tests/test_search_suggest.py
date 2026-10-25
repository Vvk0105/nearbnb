"""Suggestions under the search box: property names and areas, only from places that can really be booked."""
from tests.conftest import csrf_headers
from tests.helpers import API, live_listing
from tests.test_availability import world


async def suggest(anon, q):
    r = await anon.get(f"{API}/search/suggest", params={"q": q})
    assert r.status_code == 200, r.text
    return r.json()


async def test_a_property_name_is_suggested_as_the_guest_types_in_any_case(new_client, make_admin):
    _, _, _, prop, anon, _ = await world(new_client, make_admin)
    for typed in ("Beach", "beach hou", "BEACH HOUSE", "each Ho"):                      # the start, a different case, or the middle
        found = await suggest(anon, typed)
        assert [p["name"] for p in found["properties"]] == ["Beach House"], typed
    p = (await suggest(anon, "beach"))["properties"][0]
    assert p["id"] == prop["id"] and (p["suburb"], p["state"]) == ("Bondi", "NSW") and p["type_name"]


async def test_an_area_is_suggested_by_suburb_or_postcode_with_how_many_places_are_there(new_client, make_admin):
    admin, host, _, _, anon, _ = await world(new_client, make_admin)
    await live_listing(host, admin, name="Second Place")                                 # a second property in the same suburb
    by_suburb = await suggest(anon, "bond")
    assert by_suburb["places"] == [{"text": "Bondi, NSW", "suburb": "Bondi", "state": "NSW", "properties": 2}]
    by_postcode = await suggest(anon, "2026")
    assert [p["text"] for p in by_postcode["places"]] == ["Bondi, NSW"]
    assert by_postcode["properties"] == []                                               # a postcode matches areas, not names


async def test_nothing_matches_nothing_is_suggested(new_client, make_admin):
    _, _, _, _, anon, _ = await world(new_client, make_admin)
    assert await suggest(anon, "zzzz") == {"places": [], "properties": []}


async def test_a_typed_percent_or_underscore_is_just_a_character(new_client, make_admin):
    _, _, _, _, anon, _ = await world(new_client, make_admin)
    for typed in ("%%", "be_ch", "B%"):
        assert await suggest(anon, typed) == {"places": [], "properties": []}, typed


async def test_the_box_needs_at_least_two_letters_and_not_too_many(new_client, make_admin):
    _, _, _, _, anon, _ = await world(new_client, make_admin)
    assert (await anon.get(f"{API}/search/suggest", params={"q": "b"})).status_code == 422
    assert (await anon.get(f"{API}/search/suggest", params={"q": "x" * 81})).status_code == 422
    assert (await anon.get(f"{API}/search/suggest")).status_code == 422


async def test_only_places_a_guest_can_actually_book_are_suggested(new_client, make_admin):
    admin, host, _, prop, anon, _ = await world(new_client, make_admin)
    assert (await suggest(anon, "beach"))["properties"]
    paused = await host.post(f"{API}/host/properties/{prop['id']}/pause", headers=csrf_headers(host))
    assert paused.status_code == 200, paused.text
    assert await suggest(anon, "beach") == {"places": [], "properties": []}              # paused: hidden from guests, so hidden here
    await host.post(f"{API}/host/properties/{prop['id']}/resume", headers=csrf_headers(host))
    assert (await suggest(anon, "beach"))["properties"]


async def test_a_property_whose_host_is_suspended_is_not_suggested(new_client, make_admin):
    admin, _, host_id, _, anon, _ = await world(new_client, make_admin)
    assert (await suggest(anon, "beach"))["properties"]
    suspended = await admin.post(f"{API}/admin/hosts/{host_id}/suspend", json={"reason": "test"}, headers=csrf_headers(admin))
    assert suspended.status_code == 200, suspended.text
    assert await suggest(anon, "beach") == {"places": [], "properties": []}


async def test_a_draft_that_was_never_approved_is_not_suggested(new_client, make_admin):
    from tests.helpers import build_listing

    _, host, _, _, anon, _ = await world(new_client, make_admin)
    await build_listing(host, name="Secret Draft")
    assert (await suggest(anon, "secret"))["properties"] == []


async def test_no_login_is_needed_and_the_answer_is_small(new_client, make_admin):
    _, _, _, _, anon, _ = await world(new_client, make_admin)
    found = await suggest(anon, "be")
    assert len(found["properties"]) <= 5 and len(found["places"]) <= 5
    assert set(found) == {"places", "properties"}
