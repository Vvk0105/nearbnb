import io

from PIL import Image

from tests.conftest import PASSWORD, csrf_headers, register_and_verify

API = "/api/v1"
PDF_BYTES = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"


def png_bytes(color: str = "red", size: tuple[int, int] = (640, 480)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


async def login(client, email: str, password: str = PASSWORD) -> dict:
    r = await client.post(f"{API}/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


async def admin_client(new_client, make_admin, email: str = "admin@example.com"):
    await make_admin(email)
    c = await new_client()
    await login(c, email)
    return c


COMPLETE_APPLICATION = {
    "host_type": "individual", "legal_name": "Hannah Host", "phone": "+61 400 111 222",
    "address_line1": "1 Beach Rd", "suburb": "Bondi", "state": "NSW", "postcode": "2026", "country": "AU",
    "about": "I have hosted guests for years.",
}


async def submit_application(host_client, email: str):
    await register_and_verify(host_client, email)
    h = csrf_headers(host_client)
    assert (await host_client.post(f"{API}/host/apply", headers=h)).status_code == 201
    r = await host_client.patch(f"{API}/host/application", json=COMPLETE_APPLICATION, headers=h)
    assert r.status_code == 200, r.text
    r = await host_client.post(f"{API}/host/documents", data={"doc_type": "government_id"},
                               files={"file": ("id.png", png_bytes(), "image/png")}, headers=h)
    assert r.status_code == 201, r.text
    r = await host_client.post(f"{API}/host/application/submit", json={"accept_host_terms": True}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


async def approve_host(admin, host_id: str):
    r = await admin.post(f"{API}/admin/hosts/{host_id}/decision", json={"decision": "approve"}, headers=csrf_headers(admin))
    assert r.status_code == 200, r.text
    return r.json()


async def active_host(new_client, admin, email: str = "host@example.com"):
    """A fully active host: applied, approved, Stripe ready. Returns (client, host_id)."""
    c = await new_client()
    data = await submit_application(c, email)
    await approve_host(admin, data["id"])
    r = await c.post(f"{API}/host/stripe/dev-complete", headers=csrf_headers(c))
    assert r.status_code == 200 and r.json()["state"] == "active", r.text
    return c, data["id"]


async def approved_host_without_stripe(new_client, admin, email: str = "newhost@example.com"):
    c = await new_client()
    data = await submit_application(c, email)
    await approve_host(admin, data["id"])
    return c, data["id"]


async def add_photos(client, url: str, n: int):
    for i in range(n):
        r = await client.post(url, files={"file": (f"p{i}.png", png_bytes(["red", "green", "blue", "orange"][i % 4]), "image/png")},
                              headers=csrf_headers(client))
        assert r.status_code == 201, r.text
    return r.json()


async def build_listing(client, *, quantity: int = 1, name: str = "Beach House") -> dict:
    """Creates a property with one unit and enough photos to pass the checklist."""
    h = csrf_headers(client)
    meta = (await client.get(f"{API}/meta/listing")).json()
    r = await client.post(f"{API}/host/properties", headers=h, json={
        "name": name, "description": "A lovely place by the sea.", "type_id": meta["property_types"][0]["id"],
        "address_line1": "1 Beach Rd", "suburb": "Bondi", "state": "NSW", "postcode": "2026",
    })
    assert r.status_code == 201, r.text
    prop = r.json()
    r = await client.post(f"{API}/host/properties/{prop['id']}/units", headers=h, json={
        "name": "Entire house", "price_per_24h": 25000, "capacity": 4, "quantity": quantity,
        "amenity_ids": [meta["amenities"][0]["id"]],
    })
    assert r.status_code == 201, r.text
    await add_photos(client, f"{API}/host/properties/{prop['id']}/photos", 3)
    prop = (await client.get(f"{API}/host/properties/{prop['id']}")).json()
    await add_photos(client, f"{API}/host/units/{prop['units'][0]['id']}/photos", 1)
    return (await client.get(f"{API}/host/properties/{prop['id']}")).json()


async def live_listing(host_client, admin, **kwargs) -> dict:
    prop = await build_listing(host_client, **kwargs)
    h = csrf_headers(host_client)
    r = await host_client.post(f"{API}/host/properties/{prop['id']}/submit", headers=h)
    assert r.status_code == 200, r.text
    r = await admin.post(f"{API}/admin/properties/{prop['id']}/decision", json={"decision": "approve"}, headers=csrf_headers(admin))
    assert r.status_code == 200, r.text
    return (await host_client.get(f"{API}/host/properties/{prop['id']}")).json()
