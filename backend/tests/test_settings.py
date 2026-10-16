import pytest

from app.core.errors import AppError
from app.modules.settings import service as settings_service
from app.modules.settings.registry import REGISTRY, get_def
from tests.conftest import PASSWORD, csrf_headers

API = "/api/v1"


def test_every_default_passes_its_own_validation():
    for definition in REGISTRY.values():
        assert definition.validate(definition.default) == definition.default, definition.key


def test_validation_rejects_bad_values():
    with pytest.raises(AppError):
        get_def("checkin.interval_minutes").validate(45)
    with pytest.raises(AppError):
        get_def("finance.commission_percent").validate(150)
    with pytest.raises(AppError):
        get_def("booking.host_response_minutes").validate("soon")
    with pytest.raises(AppError):
        get_def("booking.host_response_minutes").validate(True)
    with pytest.raises(AppError):
        get_def("listing.reapproval_fields").validate(["not_a_field"])


async def test_resolver_falls_back_to_default_then_global_then_override(db, make_admin):
    admin = await make_admin()
    assert (await settings_service.get_effective(db, "booking.host_response_minutes")).source == "default"
    await settings_service.set_global(db, "booking.host_response_minutes", 120, actor_id=admin.id, reason="test", ip=None)
    value = await settings_service.get_effective(db, "booking.host_response_minutes")
    assert (value.value, value.source, value.version) == (120, "global", 1)
    overridden = await settings_service.get_effective(
        db, "booking.host_response_minutes", overrides={"booking.host_response_minutes": 45}
    )
    assert (overridden.value, overridden.source) == (45, "override")


async def test_each_change_creates_a_new_version(db, make_admin):
    admin = await make_admin()
    for minutes in (60, 90):
        await settings_service.set_global(db, "booking.payment_deadline_minutes", minutes, actor_id=admin.id, reason=None, ip=None)
    current = await settings_service.get_effective(db, "booking.payment_deadline_minutes")
    assert (current.value, current.version) == (90, 2)


async def test_max_stay_must_be_multiple_of_step(db, make_admin):
    admin = await make_admin()
    with pytest.raises(AppError):
        await settings_service.set_global(db, "booking.max_stay_hours", 100, actor_id=admin.id, reason=None, ip=None)


async def test_admin_updates_setting_and_audit_entry_is_written(client, make_admin):
    await make_admin()
    await client.post(f"{API}/auth/login", json={"email": "admin@example.com", "password": PASSWORD})
    h = csrf_headers(client)

    listing = (await client.get(f"{API}/admin/settings")).json()
    assert any(s["key"] == "checkin.interval_minutes" and s["value"] == 60 for s in listing)

    r = await client.put(f"{API}/admin/settings/checkin.interval_minutes", json={"value": 30, "reason": "pilot"}, headers=h)
    assert r.status_code == 200 and r.json()["version"] == 1
    bad = await client.put(f"{API}/admin/settings/checkin.interval_minutes", json={"value": 7}, headers=h)
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "INVALID_SETTING"
    unknown = await client.put(f"{API}/admin/settings/nope.nope", json={"value": 1}, headers=h)
    assert unknown.status_code == 404

    audit = (await client.get(f"{API}/admin/audit", params={"action": "setting.updated"})).json()
    entry = audit["items"][0]
    assert entry["entity_id"] == "checkin.interval_minutes"
    assert entry["before"]["value"] == 60 and entry["after"]["value"] == 30
    assert entry["reason"] == "pilot" and entry["actor"] == "admin@example.com"

    history = (await client.get(f"{API}/admin/settings/checkin.interval_minutes/history")).json()
    assert history[0]["value"] == 30
