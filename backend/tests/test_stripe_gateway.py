"""The Stripe Accounts v2 gateway, tested against stand-ins for Stripe's client (no network)."""
from types import SimpleNamespace

import pytest
import stripe

from app.core.errors import AppError
from app.modules.hosts import service as host_service
from app.modules.payments.stripe_gateway import (
    AccountStatus, FakeStripeGateway, RealStripeGateway, status_from_stripe_account, status_from_v2_account,
)
from tests.conftest import csrf_headers
from tests.helpers import API, admin_client, approved_host_without_stripe


def recipient(transfers: str, payouts: str) -> dict:
    return {"applied": True, "capabilities": {"stripe_balance": {"stripe_transfers": {"status": transfers}, "payouts": {"status": payouts}}}}


def entry(description: str, by: str = "user", deadline: str = "past_due", blocks: bool = True) -> dict:
    restricts = [{"capability": "stripe_balance.payouts", "configuration": "recipient", "deadline": {"status": deadline}}] if blocks else []
    return {"awaiting_action_from": by, "description": description, "errors": [], "minimum_deadline": {"status": deadline},
            "impact": {"restricts_capabilities": restricts}}


# What Stripe returned for a brand-new account, trimmed.
FRESH = {
    "applied_configurations": ["recipient"],
    "configuration": {"recipient": recipient("restricted", "restricted")},
    "requirements": {"summary": {"minimum_deadline": {"status": "past_due"}}, "entries": [
        entry("defaults.profile.business_url"), entry("external_account"), entry("identity.entity_type"),
        entry("identity.attestations.terms_of_service.account.date"),
    ]},
}


class TestStatusFromV2:
    def test_a_brand_new_account_is_not_submitted_even_while_stripe_calls_everything_eventually_due(self):
        fresh = {"configuration": {"recipient": recipient("pending", "pending")},
                 "requirements": {"entries": [entry("external_account", deadline="eventually_due")]}}
        assert status_from_v2_account(fresh).details_submitted is False

    def test_a_new_account_is_not_ready_and_lists_what_the_host_must_provide(self):
        s = status_from_v2_account(FRESH)
        assert (s.ready, s.details_submitted, s.payouts_enabled, s.transfers_active) == (False, False, False, False)
        assert s.requirements == ["defaults.profile.business_url", "external_account", "identity.entity_type",
                                  "identity.attestations.terms_of_service.account.date"]

    def test_both_capabilities_active_with_nothing_due_is_ready(self):
        s = status_from_v2_account({"configuration": {"recipient": recipient("active", "active")}, "requirements": {"entries": []}})
        assert s.ready and s.details_submitted and s.requirements == []

    def test_transfers_without_payouts_is_not_ready(self):
        assert not status_from_v2_account({"configuration": {"recipient": recipient("active", "pending")}, "requirements": {"entries": []}}).ready

    def test_waiting_on_stripe_counts_as_submitted_but_not_ready(self):
        s = status_from_v2_account({"configuration": {"recipient": recipient("pending", "pending")},
                                    "requirements": {"entries": [entry("identity.individual.id_number", by="stripe", deadline="currently_due")]}})
        assert s.details_submitted is True and not s.ready and s.requirements == []

    def test_an_optional_item_that_blocks_nothing_does_not_count_against_the_host(self):
        s = status_from_v2_account({"configuration": {"recipient": recipient("active", "active")},
                                    "requirements": {"entries": [entry("identity.business_details.extra", deadline="eventually_due", blocks=False)]}})
        assert s.details_submitted is True and s.ready and s.requirements == ["identity.business_details.extra"]

    def test_an_account_without_the_recipient_configuration_is_reported_as_unknown(self):
        assert status_from_v2_account({"configuration": {"merchant": {}}, "requirements": {"entries": []}}) is None
        assert status_from_v2_account({}) is None


class StubAccounts:
    def __init__(self, parent):
        self.parent = parent

    def create(self, params):
        self.parent.calls.append(("v2.accounts.create", params))
        return SimpleNamespace(id="acct_new")

    def retrieve(self, account_id, params=None):
        self.parent.calls.append(("v2.accounts.retrieve", account_id, params))
        return self.parent.v2_account


class StubLinks:
    def __init__(self, parent, version):
        self.parent, self.version = parent, version

    def create(self, params):
        self.parent.calls.append((f"{self.version}.links.create", params))
        failure = self.parent.link_failures.get(self.version)
        if failure:
            raise failure
        return SimpleNamespace(url=f"https://connect.stripe.test/{self.version}")


class StubV1Accounts:
    def __init__(self, parent):
        self.parent = parent

    def retrieve(self, account_id):
        self.parent.calls.append(("v1.accounts.retrieve", account_id))
        return self.parent.v1_account


class StubClient:
    def __init__(self, v2_account=None, v1_account=None, link_failures=None):
        self.calls, self.v2_account, self.v1_account, self.link_failures = [], v2_account, v1_account, link_failures or {}
        self.v2 = SimpleNamespace(core=SimpleNamespace(accounts=StubAccounts(self), account_links=StubLinks(self, "v2")))
        self.v1 = SimpleNamespace(accounts=StubV1Accounts(self), account_links=StubLinks(self, "v1"))


def gateway(client: StubClient) -> RealStripeGateway:
    g = RealStripeGateway.__new__(RealStripeGateway)
    g._client = client
    return g


class TestRealGateway:
    def test_creates_a_v2_recipient_account_with_the_express_dashboard_and_platform_responsibilities(self):
        client = StubClient()
        assert gateway(client).create_account(email="h@example.com", country="AU", host_id="h-1", display_name="Hannah Host") == "acct_new"
        (_, params), = client.calls
        assert params["contact_email"] == "h@example.com" and params["display_name"] == "Hannah Host"
        assert params["identity"] == {"country": "au"} and params["dashboard"] == "express"
        assert params["configuration"] == {"recipient": {"capabilities": {"stripe_balance": {"stripe_transfers": {"requested": True}}}}}
        assert params["defaults"]["responsibilities"] == {"fees_collector": "application", "losses_collector": "application"}
        assert params["metadata"] == {"host_id": "h-1"}

    def test_the_display_name_falls_back_to_the_email(self):
        client = StubClient()
        gateway(client).create_account(email="h@example.com", country="AU", host_id="h-1")
        assert client.calls[0][1]["display_name"] == "h@example.com"

    def test_onboarding_uses_the_v2_link_when_stripe_allows_it(self):
        client = StubClient()
        assert gateway(client).create_onboarding_link("acct_1", "https://x/refresh", "https://x/return") == "https://connect.stripe.test/v2"
        (name, params), = client.calls
        assert name == "v2.links.create" and params["use_case"]["account_onboarding"] == {"refresh_url": "https://x/refresh", "return_url": "https://x/return"}

    def test_onboarding_falls_back_to_the_v1_link_when_the_v2_link_is_refused(self):
        client = StubClient(link_failures={"v2": stripe.InvalidRequestError("Please switch to an account created in a sandbox", param="account")})
        assert gateway(client).create_onboarding_link("acct_1", "https://x/refresh", "https://x/return") == "https://connect.stripe.test/v1"
        assert [c[0] for c in client.calls] == ["v2.links.create", "v1.links.create"]
        assert client.calls[1][1] == {"account": "acct_1", "refresh_url": "https://x/refresh", "return_url": "https://x/return", "type": "account_onboarding"}

    def test_other_stripe_failures_are_not_retried_and_become_a_readable_error(self):
        client = StubClient(link_failures={"v2": stripe.APIConnectionError("network down")})
        with pytest.raises(AppError) as err:
            gateway(client).create_onboarding_link("acct_1", "r", "s")
        assert err.value.status_code == 502 and err.value.code == "STRIPE_ERROR"
        assert [c[0] for c in client.calls] == ["v2.links.create"]

    def test_status_is_read_from_the_v2_account(self):
        client = StubClient(v2_account=SimpleNamespace(to_dict=lambda: {"configuration": {"recipient": recipient("active", "active")}, "requirements": {"entries": []}}))
        s = gateway(client).retrieve_account("acct_1")
        assert s.ready
        assert client.calls == [("v2.accounts.retrieve", "acct_1", {"include": ["configuration.recipient", "requirements"]})]

    def test_an_older_v1_account_is_read_the_v1_way(self):
        v1 = {"details_submitted": True, "payouts_enabled": True, "capabilities": {"transfers": "active"}, "requirements": {"currently_due": []}}
        client = StubClient(v2_account=SimpleNamespace(to_dict=lambda: {"configuration": {}}), v1_account=v1)
        assert gateway(client).retrieve_account("acct_old").ready
        assert [c[0] for c in client.calls] == ["v2.accounts.retrieve", "v1.accounts.retrieve"]


def test_the_v1_reader_still_understands_account_updated_webhooks():
    s = status_from_stripe_account({"details_submitted": True, "payouts_enabled": True, "capabilities": {"transfers": "active"}, "requirements": {"currently_due": []}})
    assert s == AccountStatus(True, True, True, [])


def test_the_fake_gateway_accepts_the_new_argument():
    assert FakeStripeGateway().create_account(email="a@b.c", country="AU", host_id="x", display_name="A").startswith("acct_fake_")


async def test_the_host_application_name_is_sent_to_stripe_as_the_display_name(new_client, make_admin, monkeypatch):
    seen = {}

    class Recorder(FakeStripeGateway):
        def create_account(self, **kwargs):
            seen.update(kwargs)
            return super().create_account(**kwargs)

    monkeypatch.setattr(host_service, "get_gateway", lambda: Recorder())
    admin = await admin_client(new_client, make_admin)
    host, _ = await approved_host_without_stripe(new_client, admin)
    r = await host.post(f"{API}/host/stripe/onboarding-link", headers=csrf_headers(host))
    assert r.status_code == 200, r.text
    assert seen["display_name"] == "Hannah Host" and seen["country"] == "AU" and seen["email"] == "newhost@example.com"
