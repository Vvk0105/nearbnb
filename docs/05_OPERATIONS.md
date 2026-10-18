# Operations runbook

Everything here is run on the server from the project folder, unless it says otherwise.

## Is it healthy?

* `GET https://<API_DOMAIN>/api/v1/health`: the app is up.
* `GET https://<API_DOMAIN>/api/v1/health/ready`: database and Redis both answer. Returns 503 and names the broken part when not. **Point an uptime monitor (UptimeRobot or Better Stack, free tier) at `/health/ready`** and alert by email or SMS.
* Admin dashboard: alerts for failed payments, payouts, refunds, webhooks and calendar syncs. Check it daily at first.
* Logs: `docker compose -f docker-compose.prod.yml logs --tail=200 api worker beat`.

## Backups (daily) and restore

Take a backup (keeps the last 14):

    ops/backup.sh docker-compose.prod.yml /var/backups/nearbnb

Schedule it (as the deploy user, `crontab -e`):

    15 3 * * *  cd /srv/nearbnb && ops/backup.sh docker-compose.prod.yml /var/backups/nearbnb >> /var/log/nearbnb-backup.log 2>&1

**Also copy that folder off the server every day** (for example `rclone sync /var/backups/nearbnb remote:nearbnb-backups` to any cloud storage). A backup only on the same disk is lost with the server.

Uploaded photos and documents live in the `storage` volume; back that up too:

    docker run --rm -v nearbnb_storage:/d -v /var/backups:/b alpine tar czf /b/storage-$(date +%F).tgz -C /d .

**Restore drill: do it once before launch, then every quarter.** It restores a dump into a scratch database next to the live one, compares counts, then deletes the scratch database:

    ops/restore_drill.sh /var/backups/nearbnb/<file>.dump docker-compose.prod.yml

It prints `RESTORE DRILL PASSED` when counts match (they can differ if data changed since the dump).
*Local drill result (Phase 10): 4 users, 5 bookings, 4 payments and 73 audit rows restored identically, migration 0009.*

**Real disaster** (new empty server): bring the stack up, stop `api worker beat`, restore:

    docker compose exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner' < file.dump

then restore the storage tarball and start the services. With daily backups you can lose up to one day of data; ask if you want hourly.

## Deploying an update

1. Build and test locally, build the image, load it on the server (see 03_SERVER_SETUP.md).
2. **Take a backup first.**
3. `docker compose -f docker-compose.prod.yml up -d`, then `docker compose -f docker-compose.prod.yml exec api alembic upgrade head`.
4. Check `/health/ready` and the admin dashboard.

Rolling code back is easy; rolling a migration back is not, which is why step 2 exists.

## Monthly

* Dependency audit (commands in 04_SECURITY_REVIEW.md), update, run the tests.
* Check disk space (`df -h`) and that the off-server backups really exist.
* Look at the Stripe dashboard for disputes and failed payouts.

## When something breaks

| Symptom | First look | Usual fix |
|---|---|---|
| Guests paid but bookings stay "accepted" | Admin > Webhooks, Stripe > Developers > Webhooks | Fix the webhook URL/secret, press **Run again**. A job also reconciles stuck payments every few minutes. |
| Emails not arriving | worker logs; `python -m app.tools.email_check <domain> --send you@example.com` | SMTP credentials, SPF/DKIM/DMARC records. Failed emails retry 5 times with growing gaps. |
| Host not paid | Admin > Payouts | Failed: Retry after the host fixes their Stripe account. Held: an open issue or refund, release it when settled. |
| A calendar stopped syncing | Admin > Calendar | Host fixes the link, or press Try again now. Dates already blocked stay blocked. |
| Double booking suspected | Inside NearBnB it cannot happen (database rule). Look at Admin > Calendar conflicts (another site took dates) | A person decides which booking to keep; nothing is cancelled automatically. |
| Site slow | `docker stats`; `ops/loadtest.py` against a copy | The server is small (2 GB): consider a bigger plan. |

## Measured locally (Phase 10)

`ops/loadtest.py`, 40 people browsing at once for 15 to 20 s (health, search, catalogue, legal text), one API process as in production: about 90 to 100 requests per second, median 240 to 500 ms, 95th percentile about 1 s, 0 errors in the last two runs. One earlier run reported errors that I could not reproduce and that left nothing in the server log; treat it as unexplained. This was a laptop running Docker next to other things, not the real server: **repeat on staging**. The double-booking, double-payment, double-payout and webhook-replay races are covered by automated tests (simultaneous requests, simultaneous confirmations, simultaneous payout runs).

## Error reporting

There is no external error tracker yet. Failures are logged with a stack trace. If you want crash emails, create a free Sentry project and tell me: it is a small change (a `SENTRY_DSN` setting).
