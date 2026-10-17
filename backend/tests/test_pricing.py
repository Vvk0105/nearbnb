from decimal import Decimal

from app.modules.pricing.service import TaxSpec, compute_price, guest_view, money_round
from tests.conftest import csrf_headers
from tests.helpers import API, active_host, admin_client, day_local, live_listing

GST = TaxSpec("GST", 1000, False, ("accommodation", "cleaning", "service_fee"))


def price(**over):
    base = dict(price_per_24h=20000, hours=24, cleaning_fee=2000, service_fee_percent=7.5, tax=GST, commission_percent=12.0)
    return compute_price(**{**base, **over})


def test_matches_the_worked_example_in_the_requirements_document():
    """AUD 200 + 20 cleaning + 15 service fee + 23.50 tax = AUD 258.50 for the guest."""
    p = price()
    assert (p["accommodation"], p["cleaning_fee"], p["service_fee"], p["tax"], p["total"]) == (20000, 2000, 1500, 2350, 25850)
    assert p["commission"] == 2400 and p["host_payable"] == 19600  # 12% of accommodation; host keeps the rest


def test_longer_stays_multiply_the_flat_daily_price():
    p = price(hours=72)
    assert p["nights"] == 3 and p["accommodation"] == 60000 and p["service_fee"] == 4500
    assert p["tax"] == 6650 and p["total"] == 60000 + 2000 + 4500 + 6650  # cleaning is charged once, not per night


def test_tax_can_be_included_in_the_listed_price():
    p = price(tax=TaxSpec("GST", 1000, True, ("accommodation", "cleaning", "service_fee")))
    assert p["total"] == 20000 + 2000 + 1500          # nothing added on top
    assert p["tax"] == 2136                           # 10% GST portion already inside 23,500: 23500 - 23500/1.1
    assert p["tax_inclusive"] is True


def test_tax_can_apply_to_only_some_components():
    p = price(tax=TaxSpec("Levy", 500, False, ("accommodation",)))
    assert p["tax"] == 1000 and p["total"] == 20000 + 2000 + 1500 + 1000


def test_no_tax_rule_means_no_tax():
    p = price(tax=None)
    assert p["tax"] == 0 and p["total"] == 23500 and p["tax_name"] is None


def test_commission_base_and_zero_commission():
    assert price(commission_base="accommodation_and_cleaning")["commission"] == 2640  # 12% of 22,000
    p = price(commission_percent=0.0)
    assert p["commission"] == 0 and p["host_payable"] == 22000


def test_rounding_rules_are_configurable():
    # service fee 7.5% of 20,100 = 1507.5 cents
    assert price(price_per_24h=20100, tax=None, rounding="half_up")["service_fee"] == 1508
    assert price(price_per_24h=20100, tax=None, rounding="half_even")["service_fee"] == 1508  # 1507.5 -> even
    assert price(price_per_24h=20300, tax=None, rounding="half_even")["service_fee"] == 1522  # 1522.5 -> even (1522)
    assert price(price_per_24h=20100, tax=None, rounding="down")["service_fee"] == 1507
    assert money_round(Decimal("2.5"), "half_up") == 3 and money_round(Decimal("2.5"), "half_even") == 2


def test_everything_is_integer_cents_never_floats():
    p = price(price_per_24h=19999, service_fee_percent=7.5, commission_percent=12.5)
    for key in ("accommodation", "cleaning_fee", "service_fee", "tax", "total", "commission", "host_payable"):
        assert isinstance(p[key], int), key


def test_the_guest_view_never_exposes_commission_or_host_earnings():
    view = guest_view(price())
    assert "commission" not in view and "host_payable" not in view and "commission_percent" not in view
    assert view["total"] == 25850


async def test_admin_can_change_the_tax_rate_and_the_quote_follows(new_client, make_admin):
    admin = await admin_client(new_client, make_admin)
    host, _ = await active_host(new_client, admin)
    prop = await live_listing(host, admin)
    unit_id = prop["units"][0]["id"]
    anon = await new_client()
    body = {"checkin_local": day_local(10), "hours": 24, "guests": 2}
    q = (await anon.post(f"{API}/units/{unit_id}/quote", json=body)).json()
    assert q["price"]["tax_name"] == "GST" and q["price"]["tax"] == 2688  # 10% of (25000 + 1875)

    ah = csrf_headers(admin)
    rules = (await admin.get(f"{API}/admin/tax-rules")).json()
    au = next(r for r in rules if r["country"] == "AU")
    r = await admin.put(f"{API}/admin/tax-rules/{au['id']}", json={"name": "GST", "rate_percent": 15, "inclusive": False,
                                                                   "applies_to": ["accommodation"], "active": True}, headers=ah)
    assert r.status_code == 200 and r.json()["rate_percent"] == 15
    q = (await anon.post(f"{API}/units/{unit_id}/quote", json=body)).json()
    assert q["price"]["tax"] == 3750  # 15% of accommodation only
    assert q["price"]["total"] == 25000 + 1875 + 3750

    bad = await admin.put(f"{API}/admin/tax-rules/{au['id']}", json={"name": "GST", "rate_percent": 10, "applies_to": ["gold"]}, headers=ah)
    assert bad.status_code == 422
    off = await admin.put(f"{API}/admin/tax-rules/{au['id']}", json={"name": "GST", "rate_percent": 10, "active": False}, headers=ah)
    assert off.status_code == 200
    assert (await anon.post(f"{API}/units/{unit_id}/quote", json=body)).json()["price"]["tax"] == 0  # no active rule -> no tax


async def test_only_admins_can_manage_tax_rules(new_client, make_admin):
    from tests.conftest import register_and_verify

    guest = await new_client()
    await register_and_verify(guest, "taxguest@example.com")
    assert (await guest.get(f"{API}/admin/tax-rules")).status_code == 403
    assert (await guest.post(f"{API}/admin/tax-rules", json={"country": "AU", "name": "X", "rate_percent": 1}, headers=csrf_headers(guest))).status_code == 403
