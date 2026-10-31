import json

import httpx
import pytest

from app.core.config import get_settings
from app.modules.places import service as places
from tests.helpers import API

AUTOCOMPLETE_REPLY = {"suggestions": [
    {"placePrediction": {"placeId": "ChIJ_bondi_beach_abc", "text": {"text": "Bondi Beach NSW, Australia"},
                         "structuredFormat": {"mainText": {"text": "Bondi Beach"}, "secondaryText": {"text": "NSW, Australia"}}}},
    {"queryPrediction": {"text": {"text": "ignored query suggestion"}}},
]}
DETAILS_REPLY = {
    "id": "ChIJ_bondi_beach_abc", "displayName": {"text": "Bondi Beach"},
    "formattedAddress": "1 Campbell Parade, Bondi Beach NSW 2026, Australia",
    "location": {"latitude": -33.8908, "longitude": 151.2743},
    "addressComponents": [
        {"longText": "1", "shortText": "1", "types": ["street_number"]},
        {"longText": "Campbell Parade", "shortText": "Campbell Pde", "types": ["route"]},
        {"longText": "Bondi Beach", "shortText": "Bondi Beach", "types": ["locality", "political"]},
        {"longText": "New South Wales", "shortText": "NSW", "types": ["administrative_area_level_1", "political"]},
        {"longText": "2026", "shortText": "2026", "types": ["postal_code"]},
        {"longText": "Australia", "shortText": "AU", "types": ["country", "political"]},
    ],
}


@pytest.fixture
def google(monkeypatch):
    """Pretends to be Google. Records every request so tests can check what we sent."""
    settings = get_settings()
    settings.google_places_api_key = "test-key-123"
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("places:autocomplete"):
            return httpx.Response(200, json=AUTOCOMPLETE_REPLY)
        return httpx.Response(200, json=DETAILS_REPLY)

    monkeypatch.setattr(places, "get_http_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    yield seen
    settings.google_places_api_key = ""


async def test_without_a_key_the_feature_is_off_and_nothing_breaks(new_client):
    anon = await new_client()
    assert (await anon.get(f"{API}/places/status")).json() == {"enabled": False}
    r = await anon.get(f"{API}/places/autocomplete", params={"q": "bondi"})
    assert r.status_code == 200 and r.json() == {"enabled": False, "suggestions": []}
    assert (await anon.get(f"{API}/places/details/ChIJ_bondi_beach_abc")).status_code == 503


async def test_autocomplete_returns_clean_suggestions_and_limits_to_supported_countries(new_client, google):
    anon = await new_client()
    assert (await anon.get(f"{API}/places/status")).json() == {"enabled": True}
    r = await anon.get(f"{API}/places/autocomplete", params={"q": "bondi", "kind": "region", "token": "session-1"})
    assert r.status_code == 200
    assert r.json()["suggestions"] == [{"place_id": "ChIJ_bondi_beach_abc", "text": "Bondi Beach NSW, Australia",
                                         "main": "Bondi Beach", "secondary": "NSW, Australia"}]
    sent = google[0]
    body = json.loads(sent.content)
    assert body["includedRegionCodes"] == ["au"] and body["sessionToken"] == "session-1" and body["input"] == "bondi"
    assert "locality" in body["includedPrimaryTypes"]
    assert sent.headers["x-goog-api-key"] == "test-key-123"


async def test_address_mode_does_not_filter_by_kind_so_businesses_and_highways_are_found(new_client, google):
    """A host whose resort is on Google Maps must find it by name; a type filter of street addresses hid it."""
    anon = await new_client()
    await anon.get(f"{API}/places/autocomplete", params={"q": "erldunda desert", "kind": "address"})
    body = json.loads(google[0].content)
    assert "includedPrimaryTypes" not in body
    assert body["includedRegionCodes"] == ["au"]  # still only the countries we support


def test_a_business_on_a_highway_junction_gets_its_road_as_line_1():
    place = places.parse_place({
        "id": "ChIJ_erldunda", "displayName": {"text": "Erldunda Desert Oaks Resort"},
        "formattedAddress": "Stuart Hwy & Lasseter Hwy, Ghan NT 0872, Australia",
        "location": {"latitude": -25.2, "longitude": 133.2},
        "addressComponents": [
            {"longText": "Ghan", "shortText": "Ghan", "types": ["locality", "political"]},
            {"longText": "Northern Territory", "shortText": "NT", "types": ["administrative_area_level_1", "political"]},
            {"longText": "0872", "shortText": "0872", "types": ["postal_code"]},
            {"longText": "Australia", "shortText": "AU", "types": ["country", "political"]},
        ],
    })
    assert place["address"] == {"address_line1": "Stuart Hwy & Lasseter Hwy", "suburb": "Ghan", "state": "NT", "postcode": "0872", "country": "AU"}
    assert place["latitude"] == -25.2


def test_picking_a_town_leaves_line_1_empty_for_the_host_to_fill():
    place = places.parse_place({
        "id": "ChIJ_ghan", "formattedAddress": "Ghan NT 0872, Australia",
        "addressComponents": [{"longText": "Ghan", "shortText": "Ghan", "types": ["locality"]}],
    })
    assert place["address"]["address_line1"] == ""


async def test_place_details_become_our_address_fields(new_client, google):
    anon = await new_client()
    r = await anon.get(f"{API}/places/details/ChIJ_bondi_beach_abc", params={"token": "session-1"})
    assert r.status_code == 200
    assert r.json() == {
        "place_id": "ChIJ_bondi_beach_abc", "label": "1 Campbell Parade, Bondi Beach NSW 2026, Australia",
        "latitude": -33.8908, "longitude": 151.2743,
        "address": {"address_line1": "1 Campbell Parade", "suburb": "Bondi Beach", "state": "NSW", "postcode": "2026", "country": "AU"},
    }
    assert google[0].url.params["sessionToken"] == "session-1"
    assert "location" in google[0].headers["x-goog-fieldmask"]


async def test_the_api_key_is_never_sent_to_the_browser(new_client, google):
    anon = await new_client()
    for url in (f"{API}/places/status", f"{API}/places/autocomplete?q=bondi", f"{API}/places/details/ChIJ_bondi_beach_abc"):
        r = await anon.get(url)
        assert "test-key-123" not in r.text and "test-key-123" not in json.dumps(dict(r.headers))


async def test_google_failures_become_a_friendly_error_not_a_crash(new_client, monkeypatch):
    settings = get_settings()
    settings.google_places_api_key = "test-key-123"
    monkeypatch.setattr(places, "get_http_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(403, json={"error": "denied"}))))
    try:
        anon = await new_client()
        r = await anon.get(f"{API}/places/autocomplete", params={"q": "bondi"})
        assert r.status_code == 502 and r.json()["error"]["code"] == "PLACES_UNAVAILABLE"
        assert "type a place name" in r.json()["error"]["message"]
    finally:
        settings.google_places_api_key = ""


async def test_place_ids_and_queries_are_validated(new_client, google):
    anon = await new_client()
    assert (await anon.get(f"{API}/places/details/short")).status_code == 422
    assert (await anon.get(f"{API}/places/details/has spaces and ../slashes")).status_code in (404, 422)
    assert (await anon.get(f"{API}/places/autocomplete", params={"q": "a"})).status_code == 422
    assert (await anon.get(f"{API}/places/autocomplete", params={"q": "x" * 200})).status_code == 422
    assert (await anon.get(f"{API}/places/autocomplete", params={"q": "bondi", "kind": "nonsense"})).status_code == 422
    assert google == []  # nothing invalid reached Google
