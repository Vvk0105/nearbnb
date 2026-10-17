# NearBNB

Request-to-book accommodation marketplace with exact-hour availability.
Specs: [docs/01_FINAL_REQUIREMENTS.md](docs/01_FINAL_REQUIREMENTS.md), [docs/02_TECHNICAL_PLAN.md](docs/02_TECHNICAL_PLAN.md), server notes: [docs/03_SERVER_SETUP.md](docs/03_SERVER_SETUP.md).

Status: Phases 0 (setup), 1 (foundation), 2 (hosts and listings) and 3 (guest discovery) are done. Next: Phase 4 (booking requests).

## Run locally

```bash
cp .env.example .env                              # fill in values; leave SMTP_HOST empty in dev
docker compose up --build -d
docker compose exec api alembic upgrade head      # create tables
docker compose exec api python -m app.cli seed    # placeholder Terms / Privacy / Host Terms
docker compose exec api python -m app.cli create-admin --email you@example.com --name "Your Name"
```

| Service | URL |
|---|---|
| Frontend | http://localhost:5173 |
| API docs (dev only) | http://localhost:8000/docs |
| Health | http://localhost:8000/api/v1/health |

In development no email is sent. Verification and reset codes are printed in the worker log:

```bash
docker compose logs -f worker      # look for "Your NearBNB verification code is: 123456"
```

## Tests

Run against a real Postgres (a separate `nearbnb_test` database is created automatically):

```bash
docker compose exec api pytest
docker compose exec web npx tsc --noEmit       # frontend type check
docker compose exec web npm test                # frontend tests (login/logout screen updates)
```

## What exists after Phase 1

- Registration with email OTP, login, logout, refresh-token rotation with reuse detection, password reset and change, email change, account deactivation, active sessions.
- Cookie auth (httpOnly) with CSRF double-submit, rate limiting, optional Cloudflare Turnstile, account lockout, admin TOTP two-factor.
- Profile, photo upload (validated and re-encoded), social profile links validated against an admin-set domain list.
- Versioned settings engine with an admin screen (every setting from section 5 of the requirements), per-change reason and history.
- Append-only audit log (blocked at database level) with an admin viewer.
- Legal documents with versioning and forced re-acceptance when a new Terms/Privacy version is published.
- Email outbox with retry, sent by a Celery worker.

## What exists after Phase 2

- **Host application:** form, ID upload (private storage, admin-only, every view audited), Host Terms acceptance, admin approve / reject / request changes, resubmission.
- **Host state is derived:** active only when approved by admin AND payout-ready on Stripe. Suspended / restricted states hide listings and stop edits.
- **Stripe Connect (Express):** onboarding link, status refresh, signed `account.updated` webhook with replay protection. In development a fake gateway is used automatically (no keys needed). The real gateway is written but not yet exercised against Stripe.
- **Properties and units:** generic property types, units with price per 24 h, capacity, amenities, check-in window, and a quantity of identical rooms (hotels). Photos are validated, re-encoded and thumbnailed.
- **Approval workflow:** completeness checklist, admin approve / reject / request changes, public page for approved listings only (no exact address).
- **Re-approval:** which edits need admin approval is an admin setting. Edits to a live listing wait in a pending revision while guests keep seeing the approved version (photo changes too).
- **Availability blocks:** host blocks per room or all rooms, entered in the property's timezone. A database exclusion constraint makes overlapping bookings/blocks impossible even if the API is bypassed.
- **Per-host overrides** for commission, response/payment deadlines, lead time, advance window and payout timing, with audit.
- **Admin screens:** hosts queue and detail, listings queue with revisions, property types and amenities.

## What exists after Phase 3

- **Look and feel:** text logo "NearBnB" and a teal palette. Both live in one place (`frontend/tailwind.config.js` and `frontend/src/components/Logo.tsx`), so a client brand is a small edit.
- **Search:** by place name (suburb, state, postcode) or by location (latitude/longitude and radius, nearest first), with guests, price range, property type and amenity filters, sorting and paging. Only live listings by active hosts appear.
- **Availability filter:** give a check-in date/time and a number of nights and only places with a room free for the whole stay are listed, each host's own rules (lead time, advance window) applied.
- **Property page:** photos, description, rules, units, and per unit an availability panel with a colour-coded month calendar, a 24-hour day timeline, check-in time / stay length / guest pickers, and an instant answer.
- **Quotes:** available or not, with a clear reason ("Someone is staying from 25 Sep 2:00 PM until 26 Sep 2:00 PM"), alternative times, and a full price breakdown (accommodation, cleaning, service fee, tax, total). Hotels: "3 rooms free", and an explanation when no single room fits the whole stay.
- **Tax:** admin-managed rules per country (Admin > Tax). The price calculator works in whole cents and supports tax-inclusive and tax-exclusive prices, configurable rounding, and commission and host payout (used from Phase 4).
- **Google Places (optional):** address and area autocomplete through our own server, so the key never reaches the browser. Without a key everything still works with plain text search.

### Google Places setup

1. Google Cloud Console > create a project > attach a billing account (the free monthly quota applies).
2. Enable **Places API (New)**.
3. Credentials > Create credentials > API key. Restrict it to the Places API, and restrict by IP address to your server (and your own IP for development).
4. Put it in `.env` as `GOOGLE_PLACES_API_KEY=...`, then `docker compose up -d --force-recreate api`.

The Places code is covered by tests using a simulated Google. It has not been run against the real service yet.

### Trying the host flow locally

1. Register a normal user, open **Host > Start application**, fill the form, upload any PNG/JPG/PDF as ID, accept the Host Terms, submit.
2. Log in as admin, **Admin > Hosts**, approve the application.
3. As the host, open **Host > Payouts**. With no Stripe keys, use **Simulate completed Stripe setup**.
4. The host is now active: add a property, a unit, photos (property needs 3, each unit 1), and submit.
5. As admin, **Admin > Listings**, approve it. It is now visible at `/api/v1/properties/<id>`.

For real Stripe testing, set `STRIPE_SECRET_KEY` (test mode) and `STRIPE_WEBHOOK_SECRET` in `.env`, then forward webhooks with `stripe listen --forward-to localhost:8000/api/v1/webhooks/stripe`.

## Production

See [docs/03_SERVER_SETUP.md](docs/03_SERVER_SETUP.md). Frontend goes to Vercel (`frontend/vercel.json`, `frontend/.env.example`); the API runs on the server behind Caddy (`Caddyfile`, `docker-compose.prod.yml`).
