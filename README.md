# NearBNB

Request-to-book accommodation marketplace with exact-hour availability.
Specs: [docs/01_FINAL_REQUIREMENTS.md](docs/01_FINAL_REQUIREMENTS.md), [docs/02_TECHNICAL_PLAN.md](docs/02_TECHNICAL_PLAN.md), server notes: [docs/03_SERVER_SETUP.md](docs/03_SERVER_SETUP.md).

Status: Phase 0 (setup) and Phase 1 (foundation) are done.

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

## Production

See [docs/03_SERVER_SETUP.md](docs/03_SERVER_SETUP.md). Frontend goes to Vercel (`frontend/vercel.json`, `frontend/.env.example`); the API runs on the server behind Caddy (`Caddyfile`, `docker-compose.prod.yml`).
