# NearBNB MVP: Technical Plan

Companion to `01_FINAL_REQUIREMENTS.md`. Requirement numbers like "6.2" refer to that document.

---

## 1. Stack

| Layer | Choice |
|---|---|
| Backend | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 (async), Alembic |
| Database | PostgreSQL 16 with `btree_gist` (overlap constraints) and PostGIS (distance search) |
| Jobs | Celery + Celery Beat, Redis broker |
| Cache / limits | Redis (rate limiting, short-lived OTP counters) |
| Frontend | React 18 + Vite + TypeScript, Tailwind, TanStack Query, React Router |
| Payments | Stripe PaymentIntents + Payment Element, Stripe Connect Express, separate charges and transfers |
| Maps | Google Places (autocomplete, geocoding), timezone from coordinates |
| Email | SMTP (env config), Jinja2 templates, sent via Celery |
| Files | Local Docker volume behind a storage interface (S3-ready) |
| CAPTCHA | Cloudflare Turnstile |
| Infra | Docker Compose (api, worker, beat, postgres, redis, web, nginx) |
| Tests | pytest, pytest-asyncio, Playwright (key E2E), Stripe test mode and CLI |

---

## 2. Architecture

```
Browser (React SPA)
   |  HTTPS
nginx  --- /api --> FastAPI (REST, cookie auth)
   |                    |-- PostgreSQL
   |                    |-- Redis
   |                    |-- Stripe API  <-- webhooks --> /api/webhooks/stripe
   |                    |-- Google Places / SMTP
Celery worker + beat ---+-- jobs: expiry, reminders, payouts, emails, iCal
```

Modular monolith. Each domain is a Python package with its own models, schemas, service layer and router. Business logic lives in `services`, routers stay thin.

### Cross-cutting
- **Settings resolver:** `get_setting(key, host_id?, country?)` returns host override, then country, then global default. Returns a value plus the version id for snapshotting.
- **Money:** `Money(amount_minor:int, currency:str)`. No floats anywhere.
- **Audit:** `audit.record(actor, action, entity, before, after, reason)` called from services.
- **Storage:** `Storage.save/open/delete` with `LocalStorage` now, `S3Storage` later. ID documents live in a private area served only through an admin endpoint.
- **Idempotency:** `stripe_events` table (unique event id), idempotency keys on every Stripe write call.

---

## 3. Repository layout

```
nearbnb/
  docker-compose.yml
  .env.example
  docs/
  backend/
    app/
      main.py
      core/            config, security, db, redis, errors, money, time, storage
      modules/
        auth/          register, login, otp, reset, sessions, 2fa
        users/         profile, social links, consents
        hosts/         application, verification, stripe_connect, status
        properties/    property, units, rooms, media, revisions, amenities, types
        availability/  occupancy, holds, search, timeline
        bookings/      request, accept, reject, withdraw, state machine, snapshot
        pricing/       calculator, tax, fees
        payments/      payment intents, webhooks, receipts
        refunds/       cancellation policy, refund engine
        payouts/       eligibility, transfers, reconciliation
        reviews/       blind publishing, moderation
        issues/        reports
        notifications/ email templates, outbox
        ical/          adapter interface, ical adapter, export feed
        admin/         dashboards, queues, exports
        settings/      versioned settings, templates, overrides
        audit/
      jobs/            celery tasks and schedules
    alembic/
    tests/
  frontend/
    src/
      app/ routes, layouts, auth guards
      features/ auth, search, property, booking, host, admin, ...
      components/ shared UI
      lib/ api client, time (tz), money format
```

---

## 4. Data model

PK = UUID unless noted. All tables have `created_at`, `updated_at`. Money columns are integer minor units. `*_at` columns are timestamptz (UTC).

### 4.1 Identity
| Table | Key columns |
|---|---|
| users | email (unique), password_hash, full_name, phone, dob, photo_path, status, email_verified_at, is_admin, totp_secret?, last_login_at |
| user_social_links | user_id, platform, url |
| user_consents | user_id, doc_type (terms/privacy/host_terms), version, accepted_at, ip |
| otp_codes | user_id, purpose, code_hash, expires_at, attempts, consumed_at |
| sessions / refresh_tokens | user_id, token_hash, user_agent, ip, expires_at, revoked_at |

### 4.2 Hosts
| Table | Key columns |
|---|---|
| host_profiles | user_id (unique), status, application_data (jsonb), review_note, approved_at, stripe_account_id, stripe_charges_enabled, stripe_payouts_enabled, stripe_details_submitted, stripe_requirements (jsonb), active_at |
| host_documents | host_id, doc_type, storage_path, status, reviewed_by |
| host_setting_overrides | host_id, key, value (jsonb), effective_from |
| host_cancellation_events | host_id, booking_id, reason, at |

### 4.3 Inventory
| Table | Key columns |
|---|---|
| property_types | name, active |
| amenities | name, icon, active |
| properties | host_id, type_id, name, description, address fields, country, region, location (geography point), timezone, rules, checkin_instructions, cancellation_template_id, custom_tiers (jsonb), status, live_revision_id, rating_avg, rating_count |
| property_revisions | property_id, data (jsonb), status (pending/approved/rejected), submitted_at, reviewed_by, note |
| units | property_id, name, description, price_per_24h, cleaning_fee, currency, capacity, quantity, max_stay_hours?, checkin_from, checkin_to, status, ical fields |
| rooms | unit_id, label, active |
| media | owner_type, owner_id, path, position |
| unit_amenities | unit_id, amenity_id |

Re-approval-sensitive fields are tracked in `settings` (`reapproval.fields`). Edits to those create a `property_revisions` row, others apply directly.

### 4.4 Occupancy (the double-booking guard)
One table for every reason a room is unavailable:

```
room_occupancy
  id, room_id, period tstzrange, kind (hold | booking | host_block | external_block),
  booking_id?, source_id?, expires_at?, created_at
  EXCLUDE USING gist (room_id WITH =, period WITH &&)
```
- A **hold** row is replaced in place by a **booking** row type on confirmation (same period, same room, one transaction).
- Expired holds are deleted by the job and also ignored by queries (`expires_at < now()`), so a delayed job never blocks anyone. Stale holds are cleaned in the same transaction before insert.
- Host blocks cannot overlap confirmed bookings (constraint). External blocks that would overlap are skipped and written to `ical_conflicts`.

### 4.5 Bookings and money
| Table | Key columns |
|---|---|
| bookings | reference (human code), guest_id, unit_id, room_id, host_id, check_in, check_out, hours, guests, status, requested_at, responded_at, accepted_at, expires_at, payment_deadline_at, confirmed_at, cancelled_at, cancelled_by, cancel_reason, guest_snapshot (jsonb), pricing_snapshot (jsonb), policy_snapshot (jsonb), settings_version_refs |
| booking_events | booking_id, from_status, to_status, actor, at, note |
| payments | booking_id, stripe_payment_intent_id, stripe_charge_id, amount, currency, status, failure_code |
| refunds | booking_id, payment_id, amount, reason, initiated_by, approved_by, type (policy/host_cancel/exceptional/admin), status, stripe_refund_id |
| refund_requests | booking_id, host_id, amount, reason, status, reviewed_by, note |
| transfers | booking_id, host_id, amount, status, stripe_transfer_id, eligible_at, transferred_at, reversed_at, failure |
| payout_records | transfer_id, stripe_payout_id, status, arrival_date |
| stripe_events | event_id (unique), type, payload, processed_at, error |
| invoices | booking_id, number, issued_at, data (jsonb), pdf_path |

`pricing_snapshot` keeps: accommodation, cleaning, service fee %, service fee, tax name/%/amount, commission %, commission, guest_total, host_payable, currency, rounding rule, plus the settings version ids used.

### 4.6 Configuration
| Table | Key columns |
|---|---|
| settings | key, scope (global/country), scope_ref, value (jsonb), version, effective_from, created_by |
| cancellation_templates | name, tiers (jsonb: [{hours_before, refund_pct}]), is_default, active, version |
| tax_rules | country, region?, name, rate_bp, inclusive, applies_to (jsonb), effective_from |
| legal_documents | doc_type, version, body, published_at |

### 4.7 Engagement and ops
| Table | Key columns |
|---|---|
| reviews | booking_id, direction (guest_to_host / host_to_guest), author_id, subject_id, rating, comment, status, published_at, hidden_reason |
| issues | booking_id?, reporter_id, subject_type/id, category, body, status, admin_note |
| notifications_outbox | user_id, template, payload, status, attempts, send_after |
| ical_sources | unit_id, url, checkin_time, checkout_time, last_synced_at, last_error, etag |
| ical_exports | unit_id, token, created_at |
| ical_conflicts | unit_id, external_range, booking_id, status |
| audit_logs | actor_id, action, entity_type, entity_id, before, after, reason, ip, at (append-only) |

Indexes: GiST on `properties.location`, `room_occupancy`; btree on `bookings(status, expires_at)`, `bookings(host_id, status)`, `transfers(status, eligible_at)`.

---

## 5. Core engines

### 5.1 Availability and holds
- `find_free_room(unit_id, period)`: first active room with no overlapping, non-expired occupancy row.
- `create_hold(...)`: in one transaction: delete expired holds for the unit, pick room, `INSERT` occupancy row. The exclusion constraint is the final judge. On a constraint violation, try the next room, or return "not available".
- Search query: distance filter (PostGIS), unit live, capacity, `EXISTS (free room)` for `[check_in, check_in + min_hours]`.
- Timeline endpoint returns merged occupied ranges per day for a unit so the UI never exposes room-level details.

### 5.2 Booking state machine
Single `transition(booking, to_status, actor)` function with an allowed-transition table. It writes `booking_events` and an audit row, and enqueues notifications through the outbox in the same DB transaction.

### 5.3 Pricing calculator
Pure function: `(unit, hours, settings snapshot, tax rule) -> PricingSnapshot`. Used by the quote endpoint (display) and booking creation (stored). Same code means no mismatch. Unit tests cover rounding, inclusive/exclusive tax and overrides.

### 5.4 Payments (Stripe)
1. Host accepts. Booking becomes `accepted`, hold extended to the payment deadline.
2. Guest opens pay page. Server creates (or reuses) a PaymentIntent with `metadata.booking_id`, `transfer_group = booking.reference`, idempotency key = booking id + attempt.
3. Guest pays in the Payment Element.
4. Webhook `payment_intent.succeeded`: dedupe by event id, lock booking row, re-validate hold, atomically set booking `confirmed`, convert hold to booking occupancy, save charge id, issue invoice, queue emails.
5. Webhook `payment_intent.payment_failed`: record, allow retry until the deadline.
6. If payment succeeds after the hold expired (edge case): auto-refund and mark `payment_expired`, notify guest.

### 5.5 Refunds
- `RefundEngine.calculate(booking, now)` reads the policy snapshot tiers, applies the service-fee rule, returns amounts. The same function powers the UI preview and the execution.
- Executes a Stripe Refund with an idempotency key. If a transfer exists and is not reversed, creates a transfer reversal first.
- Webhooks `refund.updated/charge.refunded` update status.

### 5.6 Payouts
- Job (every 10 min) finds confirmed bookings with `anchor_time + delay <= now`, no open refund or hold flags, host active.
- Creates `Transfer(amount=host_payable, destination=host.stripe_account_id, source_transaction=charge_id, transfer_group=reference)`.
- Webhooks `transfer.*`, `payout.*` and `account.updated` update records and host status. `account.updated` also sets host `restricted` or `active`.

### 5.7 Scheduled jobs (Celery Beat)
| Job | Frequency |
|---|---|
| expire_requests_and_holds | every minute |
| expire_payment_deadlines | every minute |
| send_reminders (host response, payment, check-in, checkout) | every 5 min |
| advance_booking_status (confirmed -> checked_in -> completed) | every 5 min |
| release_payouts | every 10 min |
| open/close review windows, publish blind reviews | every 15 min |
| ical_sync_dispatcher | every 5 min (respects per-source interval) |
| outbox_sender (with retry/backoff) | every 30 s |
| cleanup (expired OTPs, sessions) | daily |
| stripe_reconcile (compare local vs Stripe for last 48 h) | hourly |

### 5.8 iCal adapter
```
class CalendarSyncAdapter(Protocol):
    def pull(source) -> list[ExternalRange]
    def push_feed(unit) -> bytes   # export
```
`IcalAdapter` ships now. Import converts all-day events to `[date_start @ external_checkin, date_end @ external_checkout]`, replaces that source's external blocks idempotently, and records conflicts. Export rounds outward to whole days. A future channel-manager adapter implements the same interface.

---

## 6. API surface (REST, `/api/v1`)

Auth is cookie-based (httpOnly access and refresh cookies) with CSRF header for unsafe methods. Role checks are dependencies on every router.

| Module | Endpoints |
|---|---|
| auth | POST register, verify-email, resend-otp, login, logout, refresh, forgot-password, reset-password; POST change-password; admin 2FA setup/verify |
| users | GET/PATCH me; social links CRUD; photo; consents; deactivate |
| hosts | POST apply; GET/PATCH application; POST documents; GET status; POST stripe/onboarding-link; GET stripe/status |
| properties (host) | CRUD property; submit; pause; archive; revision status; units CRUD + duplicate; rooms; media upload/reorder; amenities |
| availability | GET unit availability calendar; GET day timeline; POST check (quote and availability); host blocks CRUD |
| search (public) | GET /search; GET /properties/{id}; GET /units/{id}; GET /units/{id}/reviews; GET autocomplete proxy (optional) |
| bookings (guest) | POST quote; POST request; GET mine; GET {id}; POST withdraw; POST cancel; GET cancel-preview |
| bookings (host) | GET requests; POST accept; POST reject; GET reservations; POST cancel; GET {id} |
| payments | POST bookings/{id}/pay (PaymentIntent); GET receipt/invoice; POST webhooks/stripe |
| refunds | POST host refund-request; admin: GET queue, POST approve/reject |
| payouts | GET host earnings, transactions, payouts; export CSV |
| reviews | POST review; GET mine/pending; GET guest summary (host, for request) |
| issues | POST issue; GET mine |
| ical | PUT/DELETE unit source; POST sync-now; GET export feed `/ical/{token}.ics` |
| notifications | GET preferences (basic) |
| admin | hosts queue/detail/decide/suspend; documents; properties queue/revisions/decide/unpublish; users; bookings; payments; refunds; payouts (retry); reviews moderation; issues; settings and overrides; templates; tax; legal docs; audit; exports; dashboard stats |

All list endpoints are paginated and filterable. All write endpoints return typed error codes (for example `SLOT_UNAVAILABLE`, `LEAD_TIME_VIOLATION`, `PENDING_LIMIT_REACHED`).

---

## 7. Frontend structure

| Area | Main routes |
|---|---|
| Public | `/`, `/search`, `/property/:id` (availability + request), `/login`, `/register`, `/verify`, `/reset`, `/terms`, `/privacy` |
| Guest | `/account/profile`, `/account/bookings`, `/account/bookings/:id`, `/pay/:bookingId`, `/review/:bookingId` |
| Host | `/host` (dashboard), `/host/apply`, `/host/status`, `/host/properties`, `/host/properties/:id`, `/host/units/:id`, `/host/calendar`, `/host/requests`, `/host/reservations`, `/host/earnings`, `/host/payouts`, `/host/reviews`, `/host/settings` |
| Admin | `/admin` (dashboard), `/admin/hosts`, `/admin/properties`, `/admin/users`, `/admin/bookings`, `/admin/payments`, `/admin/refunds`, `/admin/payouts`, `/admin/reviews`, `/admin/issues`, `/admin/settings`, `/admin/audit`, `/admin/profile` |

Shared components: availability picker (calendar + timeline + selector), price breakdown, status badges, countdown timers, data tables with filters, file upload, address autocomplete.

---

## 8. Delivery phases

| # | Phase | Deliverables | Exit check |
|---|---|---|---|
| 0 | Setup | Repo, Docker Compose, CI lint/test, env config, base schema + Alembic | `docker compose up` runs api, web, db, redis, worker |
| 1 | Foundation | Auth (OTP, login, reset, sessions), roles, consents, settings engine + admin settings UI, audit, email outbox, storage, rate limiting, Turnstile | A user can register, verify, log in; admin can change a setting and see the audit entry |
| 2 | Host and inventory | Host application, document upload, admin review, Stripe Connect onboarding, property/unit/room CRUD, media, revisions, admin approval, availability blocks | A host reaches Active; an approved listing is live |
| 3 | Discovery | Places autocomplete, geo search, filters, property page, availability calendar/timeline/selector, quote | A guest finds a unit and sees correct free/busy for a time range |
| 4 | Booking engine | Requests, holds, accept/reject/withdraw/expiry, state machine, limits, reminders, pricing snapshot | Concurrency test: two simultaneous requests, one wins |
| 5 | Payment | PaymentIntents, pay page, webhooks, confirmation, invoice/receipt | Stripe test payment confirms booking; replayed webhook changes nothing |
| 6 | Cancel, refund, payout | Policy engine, cancel preview, refunds, host cancel, exceptional refund flow, transfers, reversals, payout tracking | Cancel at each tier gives the right amount; payout releases on schedule |
| 7 | Dashboards | Host dashboard and money pages, admin dashboards, queues, user/booking/payment screens, CSV exports | Admin can operate the full flow without DB access |
| 8 | Reviews, issues, notifications | Blind reviews, guest trust summary, moderation, issue reports, all email templates | Both reviews publish together after window or both submitted |
| 9 | iCal-lite | Import/export, sync-now, conflicts, adapter interface | Block created in a test Airbnb-style feed appears in 15 min and cannot be booked |
| 10 | Hardening | Security review, load and concurrency tests, backups and restore test, monitoring/health, deliverability test (SPF/DKIM), E2E suite, controlled real-money test | Definition of Done in section 11 of the requirements |

---

## 9. Testing strategy

- **Unit:** pricing and tax, refund tiers, state machine transitions, settings resolver, time/timezone (including DST), iCal conversion.
- **Integration (real Postgres):** overlap constraint, hold expiry, concurrent holds (N parallel requests, exactly one wins), webhook idempotency, payout and refund reversal ordering.
- **E2E (Playwright, Stripe test mode):** host onboarding to listing live; guest request to confirmation; cancel and refund; payout release (time-travel via clock override); blind reviews.
- **Security:** authorization matrix test (every endpoint x role), IDOR checks, rate-limit and OTP abuse tests, file upload validation.
- **Ops:** backup/restore drill, webhook outage replay, job failure alerting.

---

## 10. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Double booking | DB exclusion constraint + transactional holds + re-validation at confirmation |
| Payment/booking mismatch | Webhook-driven confirmation, idempotency, hourly reconcile job |
| Late payment after hold expiry | Auto-refund path (5.4 step 6) |
| Email lands in spam | SPF/DKIM/DMARC, deliverability test, relay fallback by config |
| Tax and legal misconfiguration | All values configurable and snapshotted; accountant sign-off before launch |
| iCal limits (delay, day-level) | Round outward, conflict alerts, clear host terms; adapter for later upgrade |
| Stripe Connect country constraints | Express accounts, same-country hosts for AU launch; per-country Stripe setup before expanding |
| Scope creep | Section 10 of the requirements is the exclusion list; changes go through a written change request |

---

## 11. Prerequisites needed from the client before Phase 2/5

1. Stripe account (platform) with Connect enabled, test keys now, live keys before Phase 10.
2. Google Cloud project with Places API key (restricted by domain).
3. SMTP credentials in `.env` (never committed) and DNS access for SPF/DKIM/DMARC.
4. Cloudflare Turnstile site and secret keys.
5. Final legal text for Terms, Privacy and Host Terms.
6. Accountant confirmation of tax treatment and invoice fields (ABN etc.).
7. Starting values for commission and service fee (placeholders are fine for dev).
