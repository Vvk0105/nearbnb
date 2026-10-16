# Server Setup (2 GB RAM, 1 CPU, Ubuntu 22.04)

Measured on the target server: 2 GB RAM (about 1.25 GB available), no swap, 1 CPU core, 49 GB disk (37 GB free).
Other Docker containers are already running on it, so check ports 80 and 443 before deploying (see "Port check").

## Architecture in production

```
Browser -> Vercel (React frontend, nearbnb.com)
        -> https://api.nearbnb.com -> Caddy (HTTPS) -> API container -> Postgres / Redis
                                                       Celery worker + beat
```

Use a custom domain on Vercel and a sub-domain for the API on the same registrable domain
(`nearbnb.com` + `api.nearbnb.com`). Login uses secure cookies, which browsers only accept across
the same site. With `xxx.vercel.app` talking to your own domain the cookies would be blocked.

Production `.env` values that differ from development:

```
APP_ENV=production
APP_SECRET_KEY=<long random string>
CORS_ORIGINS=https://nearbnb.com
COOKIE_DOMAIN=.nearbnb.com
API_DOMAIN=api.nearbnb.com
SMTP_HOST=mail.nearbnb.com   (plus SMTP_USERNAME / SMTP_PASSWORD)
TURNSTILE_SECRET_KEY=<from Cloudflare>
```

Vercel project environment variables: `VITE_API_BASE_URL=https://api.nearbnb.com/api`, `VITE_TURNSTILE_SITE_KEY=...`.
Vercel's free Hobby plan is for non-commercial use; use Pro (or Cloudflare Pages) for the real business.

## Memory budget (docker-compose.prod.yml)

| Service | Limit |
|---|---|
| PostgreSQL + PostGIS | 450 MB |
| API (1 uvicorn worker) | 300 MB |
| Celery worker (concurrency 1) | 250 MB |
| Celery beat | 120 MB |
| Redis | 64 MB |
| Caddy | 48 MB |
| **Total limits** | **about 1.23 GB** |

This is tight, and other containers already on the server use memory too. Plan to move to **4 GB** before real launch traffic.

## Port check (do this first)

```bash
docker ps
ss -tlnp | grep -E ':80|:443'
```

If something else already owns ports 80/443, do not start our Caddy. Either route `api.nearbnb.com` through that
existing proxy to the `api` container, or tell me and I will adapt the compose file.

## One-time server preparation

1. Add 2 GB swap (safety net against out-of-memory kills):
   ```bash
   fallocate -l 2G /swapfile && chmod 600 /swapfile
   mkswap /swapfile && swapon /swapfile
   echo '/swapfile none swap sw 0 0' >> /etc/fstab
   ```
2. Firewall: allow only 22, 80, 443 (`ufw`). SSH keys only, disable password login.
3. DNS: A record `api.nearbnb.com` -> server IP. Add SPF, DKIM and DMARC for the mail domain.
4. Time sync (`chrony` or `systemd-timesyncd`). Booking and payout logic depend on accurate clocks.

## Deploy flow

Do not build on the server (1 CPU and 2 GB cannot build images reliably).

1. On your machine: `docker build -t nearbnb-backend:latest backend/`
2. Ship it: `docker save nearbnb-backend:latest | gzip | ssh root@server "gunzip | docker load"`
3. Copy `docker-compose.prod.yml`, `Caddyfile` and `.env` to the server.
4. `docker compose -f docker-compose.prod.yml up -d`
5. First time only:
   ```bash
   docker compose -f docker-compose.prod.yml exec api alembic upgrade head
   docker compose -f docker-compose.prod.yml exec api python -m app.cli seed
   docker compose -f docker-compose.prod.yml exec api python -m app.cli create-admin --email you@nearbnb.com --name "Your Name"
   ```
6. Frontend: connect the repo's `frontend/` folder to Vercel with the environment variables above.

## Backups

Skipped for now (no real data yet). Before the first real user: a nightly `pg_dump` copied off the server,
plus a copy of the `storage` volume (photos and ID documents), and a tested restore.
