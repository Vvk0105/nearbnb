"""Google Places (New) behind our own API, so the key stays on the server and we control cost and country limits.

NOTE: written from Google's documentation and tested with a simulated Google response only. It has not been run
against the real API yet (no key at the time of writing). Test with a real key before relying on it."""
import logging
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.errors import AppError

log = logging.getLogger(__name__)
BASE = "https://places.googleapis.com/v1"
# Search by area (suburbs, cities, postcodes) vs pick an exact street address (hosts adding a property).
REGION_TYPES = ["locality", "sublocality", "postal_code", "administrative_area_level_1"]
ADDRESS_TYPES = ["street_address", "premise", "subpremise"]


def get_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=6)


def enabled() -> bool:
    return bool(get_settings().google_places_api_key)


def _headers(field_mask: str | None = None) -> dict[str, str]:
    headers = {"X-Goog-Api-Key": get_settings().google_places_api_key, "Content-Type": "application/json"}
    if field_mask:
        headers["X-Goog-FieldMask"] = field_mask
    return headers


def _unavailable(exc: Exception) -> AppError:
    log.exception("Google Places request failed")
    return AppError(502, "PLACES_UNAVAILABLE", "Address search is unavailable right now. You can still type a place name.")


async def autocomplete(query: str, *, kind: str, session_token: str | None, countries: list[str]) -> list[dict[str, str]]:
    if not enabled():
        return []
    body: dict[str, Any] = {
        "input": query,
        "includedRegionCodes": [c.lower() for c in countries][:15],
        "includedPrimaryTypes": ADDRESS_TYPES if kind == "address" else REGION_TYPES,
    }
    if session_token:
        body["sessionToken"] = session_token  # groups keystrokes + the final pick into one billed session
    try:
        async with get_http_client() as client:
            resp = await client.post(f"{BASE}/places:autocomplete", json=body, headers=_headers("suggestions.placePrediction"))
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        raise _unavailable(exc) from exc
    out = []
    for item in data.get("suggestions", []):
        p = item.get("placePrediction")
        if not p:
            continue
        structured = p.get("structuredFormat", {})
        out.append({
            "place_id": p["placeId"],
            "text": p.get("text", {}).get("text", ""),
            "main": structured.get("mainText", {}).get("text", p.get("text", {}).get("text", "")),
            "secondary": structured.get("secondaryText", {}).get("text", ""),
        })
    return out


def parse_place(data: dict[str, Any]) -> dict[str, Any]:
    comps = {}
    for c in data.get("addressComponents", []):
        for t in c.get("types", []):
            comps.setdefault(t, c)
    short = lambda t: comps.get(t, {}).get("shortText", "")  # noqa: E731
    long = lambda t: comps.get(t, {}).get("longText", "")    # noqa: E731
    street = " ".join(x for x in (long("street_number"), long("route")) if x)
    loc = data.get("location", {})
    return {
        "place_id": data.get("id"),
        "label": data.get("formattedAddress") or data.get("displayName", {}).get("text", ""),
        "latitude": loc.get("latitude"),
        "longitude": loc.get("longitude"),
        "address": {
            "address_line1": street,
            "suburb": long("locality") or long("sublocality") or long("postal_town"),
            "state": short("administrative_area_level_1"),
            "postcode": long("postal_code"),
            "country": short("country"),
        },
    }


async def details(place_id: str, *, session_token: str | None) -> dict[str, Any]:
    if not enabled():
        raise AppError(503, "PLACES_NOT_CONFIGURED", "Address search is not configured.")
    params = {"sessionToken": session_token} if session_token else {}
    try:
        async with get_http_client() as client:
            resp = await client.get(f"{BASE}/places/{place_id}", params=params,
                                    headers=_headers("id,displayName,formattedAddress,location,addressComponents"))
        resp.raise_for_status()
        return parse_place(resp.json())
    except Exception as exc:  # noqa: BLE001
        raise _unavailable(exc) from exc
