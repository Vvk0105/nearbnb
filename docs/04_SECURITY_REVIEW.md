# Security review (Phase 10)

Reviewed against the code as of Phase 10. Test names point at the automated check that keeps each item true.

## What is protected, and how

| Area | Control | Checked by |
|---|---|---|
| Who can call what | Every route is classified. A stranger gets 401/403 on everything except a short allow-list of public routes (login, register, public listings, availability, quote, legal text, webhooks). A plain guest gets 403 on every `/admin` route and cannot use host routes. A new route that forgets its login check fails the test. | `tests/test_authorization_matrix.py` |
| Own data only | A guest cannot see or change another guest's booking; a host sees only their own requests; the host's review of a guest is private; issue reports are visible only to the people involved and admins. | `test_bookings.py`, `test_reviews.py`, `test_issues_and_reminders.py`, `test_payouts.py` |
| Sessions | Short access token (15 min) and rotating refresh token in HttpOnly cookies; Argon2 password hashing; lock after repeated failures; one-time codes expire and are limited; suspension signs the user out everywhere. | `test_auth_flow.py`, `test_security.py` |
| CSRF | Any request that carries a session cookie must echo the CSRF token, on every unsafe route. | `test_authorization_matrix.py` |
| Browser headers | `nosniff`, no framing, no referrer, locked-down CSP, `no-store` on private API answers. Caddy adds HSTS. | `test_authorization_matrix.py` |
| Rate limits | Login, register, one-time codes, password reset, search, places, public calendar feeds (Redis). | per-module tests |
| Payments | Amounts always come from the stored booking, never the browser. Webhooks verify the Stripe signature; handlers are idempotent; replays and late events cannot undo a success; a wrong amount is refunded, not confirmed. | `test_payments.py`, `test_stripe_gateway.py` |
| Double booking | The database itself refuses overlapping rooms (exclusion constraint) plus a per-unit lock. | `test_bookings.py` (simultaneous requests), `test_ical.py` |
| Outbound fetching (calendar links) | https/http only, ports 80/443, no logins in URLs, every resolved address must be public, IP pinned during the fetch, redirects re-checked, size cap, must be a real calendar. | `test_ical.py` |
| Uploads | Images are re-encoded and size/type limited; documents are checked by content; private documents are served only to their owner and admins. | `test_hosts.py`, `test_properties.py` |
| CSV exports | Cells that could run as spreadsheet formulas are neutralised; every export is audited. | `test_dashboards.py` |
| Audit trail | Admin actions go to an append-only table (the database refuses updates and deletes). | `test_settings.py` and the trigger in the migrations |
| Secrets | Only in `.env` on the server (never in git or logs). Tests blank all real keys. | `conftest.py` |
| Docs endpoints | `/docs` and `/openapi.json` are off when `APP_ENV=production`. | `main.py` |

## Dependency audit

* **Python**: `pip-audit` found known problems in Pillow 11.0.0, Starlette 0.41.3, Jinja2 3.1.5, PyJWT 2.10.1, python-multipart 0.0.20 and pytest 8.3.4. All were upgraded in Phase 10 (FastAPI 0.143.5 brings the fixed Starlette; Pillow 12.3.0, Jinja2 3.1.6, PyJWT 2.15.0, python-multipart 0.0.31, pytest 9.0.3, pytest-asyncio 1.4.0). Result after: "No known vulnerabilities found". Re-run before launch and monthly:
  `docker compose exec api sh -c "pip install pip-audit && python -m pip_audit -r requirements.txt"`
* **Frontend**: `npm audit --omit=dev` reports 2 moderate issues in `react-router` 6.x (open redirect via a backslash in `<Link>`/`navigate` when the target comes from an attacker, and an issue in server-side rendering). NearBnB does not use server-side rendering, and every redirect target in the app is a fixed path or the user's own current page, never a value taken from a link someone else sends. Not exploitable here. Moving to React Router 7 is a breaking change and is scheduled with the design pass, when the screens are touched anyway.

## Known limits and decisions (honest list)

1. **Ownership checks with valid bodies** are covered per module, not by the generic sweep. The sweep proves login, admin and CSRF on every route. It sends empty bodies, so a few host routes answer 422 before their ownership check runs.
2. **Turnstile (CAPTCHA)** is skipped when no key is set (a warning is logged in production). Set the key before launch.
3. **Two-factor** exists but is optional. Consider requiring it for admins before launch.
4. **Browser E2E (Playwright) has not been run.** The whole booking life is tested through the API in `tests/test_journey.py`, and the screens by component tests. A browser pass on staging is on the go-live checklist.
5. **Real Stripe** behaviour (live Connect onboarding, real payouts, real webhooks) can only be proven with live keys on the real domain: see the go-live checklist.
6. Backups hold personal data: keep them private, and encrypted where they are stored off the server.
