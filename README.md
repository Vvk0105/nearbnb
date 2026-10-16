# NearBNB

Request-to-book accommodation marketplace with exact-hour availability.
Specs: [docs/01_FINAL_REQUIREMENTS.md](docs/01_FINAL_REQUIREMENTS.md), [docs/02_TECHNICAL_PLAN.md](docs/02_TECHNICAL_PLAN.md).

## Run locally

```bash
cp .env.example .env        # fill in values
docker compose up --build
docker compose exec api alembic upgrade head
```

| Service | URL |
|---|---|
| Frontend (Vite) | http://localhost:5173 |
| API docs | http://localhost:8000/docs |
| Health | http://localhost:8000/api/v1/health |

## Tests

```bash
docker compose exec api pytest
```

## Production (2 GB server)

Build images and the frontend off the server, then run `docker-compose.prod.yml`.
See `docs/03_SERVER_SETUP.md`.
