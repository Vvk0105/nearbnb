# Server Setup (2 GB RAM host)

Measured: 2 GB total, about 1.25 GB available, no swap.

## Memory budget (docker-compose.prod.yml)

| Service | Limit |
|---|---|
| PostgreSQL + PostGIS | 450 MB |
| API (1 uvicorn worker) | 300 MB |
| Celery worker (concurrency 1) | 250 MB |
| Celery beat | 120 MB |
| Redis | 64 MB |
| nginx | 40 MB |
| **Total limits** | **about 1.2 GB** |

This fits for early traffic but leaves little headroom. Plan to move to **4 GB** before real launch traffic.

## One-time server preparation

1. **Add 2 GB swap** (safety net against out-of-memory kills):
   ```bash
   fallocate -l 2G /swapfile && chmod 600 /swapfile
   mkswap /swapfile && swapon /swapfile
   echo '/swapfile none swap sw 0 0' >> /etc/fstab
   ```
2. Install Docker and the compose plugin.
3. Firewall: allow only 22, 80, 443 (`ufw`). Disable password SSH login, use keys.
4. TLS: terminate with Let's Encrypt (certbot on host, or put Caddy in front of nginx).
5. DNS: point the domain at the server. Add SPF, DKIM and DMARC records for email.
6. Time sync (`chrony` or `systemd-timesyncd`). Booking logic depends on accurate clocks.

## Deploy flow

Do **not** build on the server (a frontend build or image build can exhaust 2 GB).

1. On your machine or CI: `docker build -t nearbnb-backend:latest backend/` and `npm run build` in `frontend/`.
2. Ship the image (`docker save | ssh server docker load`, or a registry) and `frontend/dist`.
3. On the server: `docker compose -f docker-compose.prod.yml up -d` then run migrations
   `docker compose -f docker-compose.prod.yml exec api alembic upgrade head`.

## Backups

- Nightly `pg_dump` to a file, copied **off the server** (object storage or another host).
- Back up the `storage` volume (photos, ID documents).
- Test a restore before launch.

## Still needed from the server owner

- CPU core count (`nproc`), disk (`df -h`), OS version (`cat /etc/os-release`).
- Whether other sites run on the same server (the hostname suggests it may be shared).
- Root or sudo access and the domain name.
