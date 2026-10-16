import io

from PIL import Image

from tests.conftest import PASSWORD, csrf_headers, latest_code, register_and_verify

API = "/api/v1"


async def test_profile_update_validates_phone_and_age(client):
    await register_and_verify(client)
    h = csrf_headers(client)
    assert (await client.patch(f"{API}/me", json={"phone": "abc"}, headers=h)).json()["error"]["code"] == "INVALID_PHONE"
    assert (await client.patch(f"{API}/me", json={"dob": "2015-01-01"}, headers=h)).json()["error"]["code"] == "UNDERAGE"
    r = await client.patch(f"{API}/me", json={"phone": "+61 400 000 000", "dob": "1990-05-20"}, headers=h)
    assert r.status_code == 200 and r.json()["dob"] == "1990-05-20"


async def test_social_links_validated_against_allowed_domains(client):
    await register_and_verify(client)
    h = csrf_headers(client)
    ok = await client.post(f"{API}/me/social-links", json={"url": "instagram.com/jane.doe"}, headers=h)
    assert ok.status_code == 201
    link = ok.json()["social_links"][0]
    assert link["platform"] == "Instagram" and link["url"] == "https://instagram.com/jane.doe"

    for url, code in [
        ("https://evil.com/jane", "SOCIAL_DOMAIN_NOT_ALLOWED"),
        ("https://instagram.com.evil.com/jane", "SOCIAL_DOMAIN_NOT_ALLOWED"),
        ("https://instagram.com/", "SOCIAL_URL_INVALID"),
    ]:
        r = await client.post(f"{API}/me/social-links", json={"url": url}, headers=h)
        assert r.status_code == 422 and r.json()["error"]["code"] == code, url

    dup = await client.post(f"{API}/me/social-links", json={"url": "https://www.instagram.com/jane.doe"}, headers=h)
    assert dup.status_code == 409
    after = await client.delete(f"{API}/me/social-links/{link['id']}", headers=h)
    assert after.json()["social_links"] == []


async def test_email_change_requires_password_and_code(client):
    await register_and_verify(client)
    h = csrf_headers(client)
    bad = await client.post(f"{API}/me/email/change", json={"new_email": "new@example.com", "password": "bad"}, headers=h)
    assert bad.status_code == 400
    ok = await client.post(f"{API}/me/email/change", json={"new_email": "new@example.com", "password": PASSWORD}, headers=h)
    assert ok.status_code == 202
    code = await latest_code("new@example.com")
    r = await client.post(f"{API}/me/email/confirm", json={"code": code}, headers=h)
    assert r.status_code == 200 and r.json()["email"] == "new@example.com"


async def test_photo_upload_accepts_images_and_rejects_other_files(client):
    await register_and_verify(client)
    h = csrf_headers(client)
    buf = io.BytesIO()
    Image.new("RGB", (800, 600), "red").save(buf, format="PNG")
    r = await client.post(f"{API}/me/photo", files={"file": ("a.png", buf.getvalue(), "image/png")}, headers=h)
    assert r.status_code == 200
    url = r.json()["photo_url"]
    assert url.startswith("/api/v1/media/public/avatars/")
    served = await client.get(url)
    assert served.status_code == 200 and served.headers["content-type"] == "image/webp"

    bad = await client.post(f"{API}/me/photo", files={"file": ("a.png", b"not an image", "image/png")}, headers=h)
    assert bad.status_code == 415


async def test_media_route_blocks_path_traversal_and_private_files(client):
    assert (await client.get(f"{API}/media/public/%2e%2e/private/x.png")).status_code == 404
    assert (await client.get(f"{API}/media/public/missing.png")).status_code == 404


async def test_pending_consent_after_new_terms_version(client, make_admin):
    user = await register_and_verify(client, email="g2@example.com")
    assert user["pending_consents"] == []
    client.cookies.clear()
    await make_admin()
    await client.post(f"{API}/auth/login", json={"email": "admin@example.com", "password": PASSWORD})
    pub = await client.post(
        f"{API}/admin/legal/terms", json={"title": "Terms v2", "body": "new text"}, headers=csrf_headers(client)
    )
    assert pub.status_code == 201 and pub.json()["version"] == 2
    client.cookies.clear()
    await client.post(f"{API}/auth/login", json={"email": "g2@example.com", "password": PASSWORD})
    me = (await client.get(f"{API}/me")).json()
    assert [c["doc_type"] for c in me["pending_consents"]] == ["terms"]
    accepted = await client.post(f"{API}/me/consents", json={"doc_type": "terms"}, headers=csrf_headers(client))
    assert accepted.json()["pending_consents"] == []
