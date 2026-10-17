from celery import Celery

from app import models as _models  # noqa: F401  (all tables must be registered in worker processes too)
from app.core.config import get_settings

settings = get_settings()

celery_app = Celery("nearbnb", broker=settings.redis_url, backend=settings.redis_url, include=["app.jobs.tasks"])
celery_app.conf.update(
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_default_queue="default",
    broker_connection_retry_on_startup=True,
    beat_schedule={
        "send-pending-emails": {"task": "emails.send_pending", "schedule": 30.0},
        "expire-and-remind-bookings": {"task": "bookings.expire_and_remind", "schedule": 60.0},
        "reconcile-payments": {"task": "payments.reconcile", "schedule": 60.0},
        "release-payouts": {"task": "payouts.release", "schedule": 600.0},
        "advance-bookings": {"task": "bookings.advance", "schedule": 60.0},
        "process-reviews": {"task": "reviews.process", "schedule": 900.0},
        "cleanup-auth-data": {"task": "auth.cleanup", "schedule": 86400.0},
    },
)


@celery_app.task(name="system.ping")
def ping() -> str:
    return "pong"
