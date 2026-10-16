# NearBNB MVP: Final Frozen Requirements

Status: FROZEN for MVP development (v1.0)
Sources: Client Scope doc, Internal Product & Technical doc, and the decisions made in the planning discussion.
Where this document and the source PDFs differ, **this document wins**.

---

## 1. Product summary

NearBNB is a web accommodation marketplace with three user roles (Guest, Host, Admin).
Bookings are **request-to-book**, use **exact-hour availability**, **flexible check-in time** and **stays in 24-hour multiples**.
Launch market is Australia (AUD), architecture is multi-country ready.

Core journey:
host approved + Stripe ready, then property/unit approved, then guest finds free time, then request (slot held),
then host accepts, then guest pays, then confirmed, then stay, then host payout, then blind reviews.

### Design principles
1. Everything business-related is **configurable from the admin panel** (section 5). No hard-coded fees, times or rules.
2. Settings resolve as: **host override, then country, then global default**. Bookings store a **snapshot** of every value used.
3. Money is stored as **integer minor units** (cents) with a currency code.
4. Timestamps are stored in **UTC**; displayed in the **property's timezone**.
5. Availability is enforced **in the database** (overlap constraint), never only in the UI.
6. Payment success comes from the **Stripe webhook**, never from the browser redirect.
7. Financial and booking history is never hard-deleted.

---

## 2. Roles

| Role | Description |
|---|---|
| Visitor | Not logged in. Browse and search only. |
| Guest | Registered, email-verified user. Every user starts as a Guest. |
| Host | A Guest who applied and was approved. Has a host state (see 3.2). |
| Super Admin | Platform operator. Only admin role in MVP. Permissions are flag-based so a Support role can be added later. |
| System | Automated jobs and webhooks. |

A single account can be both Guest and Host. A Host can also book other hosts' properties (not their own).

---

## 3. State models

### 3.1 User
`unverified` -> `active` -> `suspended` (admin reason recorded) -> `active` | `deactivated` (soft delete)

### 3.2 Host
```
none -> applied (draft) -> pending_review -> changes_requested -> pending_review
                                          -> rejected
                                          -> approved -> active
active -> restricted (Stripe issue) -> active
active/approved -> suspended (admin) -> active
```
**Active = admin approved AND identity verified AND Stripe payout-ready.**
An `approved` (not yet active) host may **create and edit draft properties** but cannot **submit** them and cannot receive bookings.

### 3.3 Property / Unit (listing)
`draft` -> `submitted` -> `approved` (live) | `rejected` | `changes_requested`
`approved` -> `paused` (host) | `unpublished` (admin) | `archived`
Only `approved` + not paused + host `active` appear in search.

### 3.4 Booking
```
requested -> accepted (awaiting_payment) -> payment_pending -> confirmed -> checked_in -> completed
requested -> rejected | expired | withdrawn
accepted/payment_pending -> payment_expired | payment_failed(retryable until deadline)
confirmed -> cancelled_by_guest | cancelled_by_host | cancelled_by_admin
```
Refund state is separate: `none | pending | partial | refunded | failed`.
`checked_in` and `completed` are set **automatically by time**. There is no manual check-in button.

### 3.5 Payment / Refund / Payout
- Payment: `created -> processing -> succeeded | failed | cancelled`
- Refund: `requested -> approved -> pending -> succeeded | failed` (or `rejected`)
- Host payout: `not_eligible -> eligible -> transferred -> paid_out | failed | reversed`

### 3.6 Review
`pending (window open) -> submitted -> published | hidden (admin moderation)`

### 3.7 Issue report
`open -> in_progress -> resolved | dismissed`

---

## 4. Property and unit model

```
Property (type: house / flat / hotel / villa / ...)
  -> Unit / room type (name, price per 24h, capacity, quantity N)
        -> Rooms (N internal rooms, optional host label e.g. "101")
              -> Occupancy (hold / booking / host block / external block)
```
- Property types are an **admin-managed list**.
- A house or flat has one unit with `quantity = 1`.
- A hotel room type has `quantity = N`. Guests see **one** listing. The system assigns a free room at hold time.
- A room is available again automatically at checkout time. No manual "mark clean" step. No buffer in MVP.
- Price is **one flat price per 24 h** per unit. No weekend or seasonal pricing.

---

## 5. Admin-configurable settings

All values are versioned (`effective_from`), and changes apply to **new bookings only**.

| Setting | Default | Scope |
|---|---|---|
| Check-in interval | 60 min (15/30/60) | Global |
| Host response window (request expiry) | 30 min | Global + per-host override |
| Guest payment deadline after acceptance | 30 min | Global + per-host override |
| Min lead time before check-in | 2 h | Global + per-host override |
| Max advance booking window | 6 months | Global + per-host override |
| Max stay | 168 h (7 days) | Global + per-property override |
| Stay duration step | 24 h | Global |
| Platform commission % | placeholder 12% | Global + per-host override |
| Commission base | accommodation only | Global |
| Guest service fee % | placeholder 7.5% | Global |
| Tax name / rate / inclusive-exclusive / applies-to | AU: GST 10% | Per country |
| Payout anchor | check-in | Global + per-host override |
| Payout delay | 24 h | Global + per-host override |
| Cancellation templates (tiers) | Flexible / Moderate / Strict | Admin defines |
| Default cancellation template | Moderate | Global |
| Cancellation customisation guardrails | no stricter than Strict | Global |
| Service fee refundable on cancellation | no | Global |
| Re-approval rules (per listing field on/off) | see 6.4 | Global |
| Required guest fields (name, DOB, phone, social URL) | all required | Global |
| Min social profiles | 1 | Global |
| Allowed social platforms/domains | Instagram, Facebook, LinkedIn, X | Global |
| Min guest age | 18 | Global |
| Max active pending requests per guest | 3 | Global |
| Review window | 14 days | Global |
| Host-cancellation alert threshold | 3 in 30 days | Global |
| Max photos per listing, max file size | 20 / 8 MB | Global |
| iCal poll interval | 15 min | Global |
| Terms / Privacy / Host Terms versions | v1 | Global |

---

## 6. Business rules

### 6.1 Search and availability
- Search inputs: location (autocomplete), check-in date/time, guest count. Optional: duration, price range, property type, amenities.
- A result appears only if some room of the unit can fit `[check_in, check_in + min duration]` and capacity >= guests.
- Availability view on the unit page:
  1. Calendar overview with green (free), amber (partial), red (full).
  2. Selected-day timeline showing occupied/open ranges.
  3. Check-in time picker (interval from settings, within the unit's allowed check-in hours).
  4. Duration picker (24, 48, 72 ... up to max stay).
  5. Instant "Available / Not available" with a conflict message ("Someone is staying from 25 Sep 2:00 PM until 26 Sep 2:00 PM") and alternative periods.
- Each unit has **allowed check-in hours** (e.g. 08:00 to 22:00, property local time).
- All times are shown in the property's timezone with a label.

### 6.2 Booking request
1. Login required (selection preserved through login).
2. Email must be verified. Profile must contain the required guest fields. At least one social URL is required, auto-filled from the profile. If missing, an error blocks the request.
3. Guest must have fewer than the max active pending requests.
4. Server validates: listing live, capacity, lead time, advance window, allowed hours, interval, duration multiple, max stay, availability.
5. Server creates the request and a **hold** on one free room for the exact range. Hold expires at `now + host response window` (never later than check-in).
6. The host is emailed. A reminder is sent at 50% of the window.
7. Guest can **withdraw** a pending request.
8. Host accepts or rejects (optional reason). No response means `expired`. Rejected, expired or withdrawn releases the hold.
9. Accept re-validates the hold, sets `accepted`, and extends the hold to `now + payment deadline`. The guest is emailed to pay.
10. The guest pays through Stripe. A payment reminder is sent at 50% of the deadline.
11. Webhook success re-validates and converts the hold to a **confirmed** booking atomically. Failure or expiry releases the hold.
12. After confirmation: host sees full guest details, guest sees host contact and **check-in instructions**.

No messaging between guest and host in MVP.

### 6.3 Pricing and fees (snapshotted on every booking)

```
accommodation = unit_price_per_24h x (hours / 24)
cleaning_fee  = flat per booking (host optional)
service_fee   = service_fee_% x accommodation
tax           = tax_% x (accommodation + cleaning_fee + service_fee)   [configurable base]
guest_total   = accommodation + cleaning_fee + service_fee + tax
commission    = commission_% x accommodation                           [configurable base]
host_payable  = accommodation + cleaning_fee - commission
```
Stripe processing and Connect fees are a platform cost. Rounding rule is configurable per country (default: round half up to the cent). Tax responsibility (platform vs host) is an accountant decision handled in config. Tax invoice fields (ABN etc.) are also configurable.

### 6.4 Listing changes and re-approval
- Admin toggles, per field, whether an edit requires re-approval.
- Suggested defaults: **on** = address, property type, photos, description. **off** = price, capacity, amenities, cancellation template.
- A re-approval edit is stored as a **pending revision**. The previous approved version stays live until the revision is approved.
- Changes never affect confirmed bookings (snapshots).

### 6.5 Cancellation and refunds
- **Guest cancels:** system shows the policy-based refund (hours before check-in -> %), guest confirms, booking cancelled, Stripe refund, status tracked.
- **Host cancels a confirmed booking:** reason required, guest gets 100% refund, cancellation recorded and counted. No automatic penalty.
- **Admin cancels:** reason required, refund per admin choice (policy or full), audit logged.
- **Exceptional host refund:** host submits request with reason, admin approves or rejects, Stripe refund if approved.
- A refund always references the original payment. If the host transfer was already made, the transfer is **reversed** before or with the refund.
- Cancellation policy is a **versioned snapshot** on the booking.
- No-show means normal flow: no refund, payout proceeds.
- Date change is not supported. Guest cancels and rebooks.

### 6.6 Payout
- Eligible at `anchor_time + delay` (default check-in + 24 h), per-host overridable.
- A job creates a Stripe **Transfer** to the host's connected account (separate charges and transfers model, linked to the original charge).
- Transfer and bank payout are tracked **separately**. Stripe controls the bank payout schedule.
- Payout is blocked while the booking has an open refund, an open issue flagged by admin, or the host is suspended/restricted.
- Failures are retried and surfaced to the admin.

### 6.7 Reviews
- Opens when the booking is `completed`, closes after the review window (14 days).
- One review per party per booking. Rating 1-5 plus comment.
- **Blind publishing:** both reviews become visible after both are submitted or when the window closes.
- Guest -> property/host: **public** on the property page, feeds the rating.
- Host -> guest: visible **only to hosts who receive a request from that guest**, and to admin. **The guest cannot see it.**
- Admin can hide or restore with a reason. No host replies.

### 6.8 Host cancellation tracking
Admin sees per host: total host cancellations, last 30 and 90 days, reasons, dates. An alert is raised at the configured threshold. Suspension is a manual admin decision.

### 6.9 Calendar sync (iCal-lite)
- Per **single-room unit** (`quantity = 1`). Host pastes an external iCal URL and sets the external check-in and checkout time.
- Import every 15 min (configurable), plus a "Sync now" button. Events become **external blocks**.
- Export: each unit has a private tokenised feed URL. Our bookings are **rounded outward to whole days** (never under-block).
- Conflicts with confirmed bookings are never auto-resolved. Host and admin are alerted.
- Built behind an **adapter interface** so a channel-manager integration can be added later.

### 6.10 Verification
- **Guest:** verified email, full name, date of birth (>= 18), phone (not OTP-verified in MVP), >= 1 social URL (format and domain validated, ownership not verified).
- **Host:** application form, ID document upload (private storage, admin-only), admin review, plus Stripe Connect KYC.
- Host sees full guest verification details **after confirmation**. Before that, a limited view (name, age, rating summary, host reviews).

### 6.11 Legal acceptance
Required checkbox for Terms and Privacy at signup, plus Host Terms at host application. Stores version, timestamp, IP. A new version forces re-acceptance on next login.

### 6.12 Anti-abuse
Rate limiting (login, signup, OTP, password reset, request creation), OTP expiry and attempt limits, resend cooldown, Cloudflare Turnstile CAPTCHA on signup/login/reset, strong password hashing, admin 2FA (TOTP).

---

## 7. Features by role

### 7.1 Visitor
Landing, search, filters, property and unit pages, availability view, pricing breakdown preview, public reviews, signup/login/reset, static pages (Terms, Privacy, Cancellation, Help).

### 7.2 Guest
| Area | Features |
|---|---|
| Account | Register, verify email (OTP/link), resend, login, logout, session expiry, forgot/reset password, change password, change email (re-verify), deactivate account |
| Profile | Name, phone, DOB, photo, social URLs (multiple) |
| Discovery | Search, filters, sort, pagination, property/unit page, availability view, price breakdown |
| Request | Create, view status, withdraw, see expiry countdown |
| Payment | Pay accepted request (Stripe), retry on failure, receipt/tax invoice |
| Bookings | Tabs: Requests, Upcoming, Past, Cancelled. Detail with times, breakdown, policy, host contact and instructions after confirmation |
| Cancel | Policy-based refund preview, confirm, refund status |
| After stay | Review host/property, raise an issue |
| Notifications | Email for all events in section 8 |

### 7.3 Host
| Area | Features |
|---|---|
| Onboarding | Become a host, application form, ID upload, status tracker, respond to change requests, Stripe Connect onboarding, re-open Stripe link |
| Properties | Create/edit (draft allowed when approved), photos, address, amenities, rules, cancellation template (within guardrails), check-in instructions, pause, archive, submit for approval (Active only), see revision status |
| Units | Create/edit/duplicate, price per 24h, capacity, quantity, room labels, cleaning fee, allowed check-in hours, photos, amenities |
| Availability | Calendar and timeline, block/unblock ranges, see holds/bookings/external blocks, conflict warnings |
| Requests | Inbox, accept/reject with reason, countdown, guest verification and host-review summary |
| Reservations | Lists by status, detail, cancel (reason required) |
| Money | Transactions, earnings, refunds, payout status and history, request exceptional refund, CSV export |
| Reviews | Review guest (private), see own property reviews |
| iCal | Add external URL, set times, Sync now, copy export URL |
| Dashboard | Pending requests, upcoming/current stays, monthly earnings, next payout, listing statuses |
| Support | Report issue |

### 7.4 Super Admin
| Module | Features |
|---|---|
| Auth/Profile | Login, 2FA, password change, session info |
| Dashboard | KPIs and alerts: pending applications, pending listings, refund requests, failed payments/payouts/webhooks, open issues, host cancellation alerts, iCal conflicts |
| Users | Search, view, history, suspend/reactivate with reason |
| Hosts | Application queue (approve/reject/request changes), ID documents, Stripe status, per-host overrides, cancellation history, suspend/reactivate |
| Properties/Units | Review submissions and revisions, approve/reject/request changes, unpublish, manage property types and amenities |
| Bookings | Search all, filter, timeline, financials, cancel/override with audit |
| Payments | All payments, provider references, failures |
| Refunds | Queue, approve/reject, provider outcome |
| Payouts | Payable amounts, transfers, status, failures, retry |
| Reviews | View, hide/restore with reason |
| Issues | List, status, notes |
| Configuration | All settings in section 5, cancellation templates, tax, legal versions |
| Audit | Immutable log with filters |
| Export | CSV of bookings, payments, refunds, payouts |

### 7.5 System (automated)
Hold/request/payment expiry, reminders, booking status transitions by time, payout eligibility and transfer, Stripe webhooks (idempotent), refund reconciliation, review window open/close, iCal sync, email sending with retry, audit writing.

---

## 8. Email notifications

Email verification, password reset, host application status (submitted, approved, rejected, changes requested), Stripe action needed, listing submitted/approved/rejected/changes requested, new booking request (host), request reminder (host), accepted/rejected/expired/withdrawn, payment reminder, payment success/failure, booking confirmation (both), cancellation and refund status, exceptional refund decision, check-in and checkout reminders, review invitation and reminder, payout sent (host), iCal conflict (host), admin alerts.

---

## 9. Non-functional requirements

Password hashing (argon2), server-side authorization on every endpoint, TLS in production, rate limiting, secrets in environment config, no raw card data, UTC storage, integer money, DB overlap constraint, idempotent webhooks/refunds/transfers, soft delete for linked records, structured logs without sensitive data, health checks, scheduled DB backups with restore test, pagination on lists, responsive web, private storage for ID documents.

---

## 10. Explicitly out of MVP

Native apps, guest-host messaging, dispute case management, AI/dynamic pricing, loyalty/referrals, promoted listings, group/multi-property bookings, split payments, multi-language and multi-currency UX, SMS/push, instant book, weekend/seasonal pricing, booking date changes or extensions, discounts/coupons, review replies, turnaround buffers, automated ID verification, phone OTP, channel-manager API.

---

## 11. Definition of done

A controlled real-money test passes end to end: host application, approval and Stripe setup; property and unit approval; exact-hour availability; request and hold; host acceptance; guest payment; confirmed booking; stay completion; configured payout; cancellation and refund; blind reviews. Admin operates everything without database access. Concurrency (double booking), webhook replay and expiry scenarios are tested.
