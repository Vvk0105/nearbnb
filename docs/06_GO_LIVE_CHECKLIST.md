# Go-live checklist

Tick each box in order. Items marked **(you)** need your accounts, domain or money; the rest are already done and tested in the code.

## 1. Accounts and keys (you)
- [ ] Domain bought; DNS for `API_DOMAIN` (A record to the server) and the website domain (Vercel).
- [ ] Server set up as in `03_SERVER_SETUP.md`; `.env` filled on the server only (never in git).
- [ ] Stripe platform account activated for live payments, Connect enabled. Live keys put in the server `.env`: `STRIPE_SECRET_KEY` (sk_live), `STRIPE_PUBLISHABLE_KEY` (pk_live), `STRIPE_WEBHOOK_SECRET`.
- [ ] Stripe webhook endpoint `https://<API_DOMAIN>/api/v1/webhooks/stripe` created in live mode with the payment, refund, transfer and account events the app handles; its signing secret is the `STRIPE_WEBHOOK_SECRET` above.
- [ ] Google Places key (restricted to the website domain) in `.env`.
- [ ] Cloudflare Turnstile keys set (`TURNSTILE_SECRET_KEY` and the site key in the frontend). Without them the sign-up form has no bot check.
- [ ] `APP_ENV=production`, a long random `APP_SECRET_KEY`, correct `APP_BASE_URL` and `CORS_ORIGINS` (website address only).
- [ ] Admin settings reviewed in the admin screens: commission and service fee (real values), payout delay, cancellation templates, review window, invoice seller name and ABN.
- [ ] Final Terms, Privacy and Host Terms text published (Admin > Legal). Accountant has confirmed the tax rules and invoice fields.

## 2. Email deliverability (you, with the tool)
- [ ] SPF, DKIM and DMARC records added as your mail provider shows.
- [ ] On the server: `docker compose -f docker-compose.prod.yml exec api python -m app.tools.email_check <your-domain> --selector <dkim-selector> --send <your-personal-email>`
- [ ] Every line is PASS (a DMARC `p=none` WARN is fine to start). The test email arrived in the **inbox**, not spam. Also send to a Gmail and an Outlook address.

## 3. Safety nets
- [ ] `ops/backup.sh` scheduled with cron and a daily copy off the server (see `05_OPERATIONS.md`).
- [ ] **Restore drill run on the server** and it says RESTORE DRILL PASSED.
- [ ] Uptime monitor on `/api/v1/health/ready` with email or SMS alerts.
- [ ] `/docs` and `/openapi.json` return 404 on the live API.
- [ ] Repeat `ops/loadtest.py` against a staging copy; no errors.
- [ ] Dependency audit repeated (commands in `04_SECURITY_REVIEW.md`).
- [ ] Consider requiring two-factor for admin accounts.

## 4. Browser pass on the live site (you, 30 minutes)
Playwright browser tests were **not** run by me (the booking life is covered end to end through the API in `backend/tests/test_journey.py`, and screens by component tests). Click through once on a real phone and a laptop: sign up, log in, search, open a listing, check availability, request, host accepts, pay, see the confirmation, check emails, host dashboard, admin dashboard.

## 5. Controlled real-money test (you, this is the "Definition of done")
Use two real people you trust (one host, one guest) and a small real amount (for example a $5 to $20 stay). Do this in live mode, on the live site.
- [ ] Host applies, uploads ID, admin approves; host completes Stripe live onboarding (bank details); status shows payable.
- [ ] Host creates a property and unit (exact-hour availability); admin approves it; it appears in search.
- [ ] Guest completes profile and requests an exact-hour stay; dates are held; the host and the guest get emails.
- [ ] Host accepts; guest pays with a real card; booking becomes Confirmed; invoice number appears; both get the confirmation email with contact details.
- [ ] A second guest cannot book those hours (try it).
- [ ] Stay completes (or move the dates in agreement with the test: the real clock is the only way, so use a stay that starts soon). After the payout delay, the host's payout shows Paid in Admin > Payouts and the money lands in the host's bank (Stripe pays out on its own schedule).
- [ ] Both leave reviews; neither is visible until both are in; then both publish.
- [ ] Second small booking: guest cancels inside the refund window; refund appears in Stripe and the guest's statement; the payout is not created or is reduced as the cancellation template says.
- [ ] Admin Exports: bookings, payments, refunds and payouts CSVs match the Stripe dashboard.
- [ ] Webhook replay: in Stripe, resend one event; nothing changes twice.
- [ ] Refund the small test amounts back / close the test; note what you saw.

## 6. Definition of Done map (from `01_FINAL_REQUIREMENTS.md`, section 11)

| Requirement | Proven by |
|---|---|
| Host application, approval, Stripe setup | `test_hosts.py`, step 5 above (live) |
| Property and unit approval | `test_properties.py` |
| Exact-hour availability | `test_availability.py`, `test_pricing.py` |
| Request and hold | `test_bookings.py` |
| Host acceptance, guest payment, confirmed booking | `test_payments.py`, `tests/test_journey.py` |
| Stay completion, configured payout | `test_payouts.py`, `tests/test_journey.py` |
| Cancellation and refund | `test_cancellations.py`, step 5 (live) |
| Blind reviews | `test_reviews.py`, `tests/test_journey.py` |
| Admin runs everything without database access | `test_dashboards.py`, admin screens for every queue (hosts, listings, bookings, payments, payouts, webhooks, issues, reviews, calendar, settings, exports) |
| Concurrency (double booking) | `test_bookings.py` (simultaneous requests and fills), `test_payments.py` (two confirmations), `test_payouts.py` (two payout runs), `test_ical.py` |
| Webhook replay | `test_payments.py` (replay, late failure, wrong amount) |
| Expiry scenarios | `test_bookings.py`, `test_payments.py` (unanswered request, unpaid acceptance, stale hold) |
| Real-money run | **Not done by me: section 5, with live keys.** |
