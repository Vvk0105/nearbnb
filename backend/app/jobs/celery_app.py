from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery("nearbnb", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_default_queue="default",
    beat_schedule={},  # populated per phase (expiry, reminders, payouts, iCal ...)
)


@celery_app.task(name="system.ping")
def ping() -> str:
    return "pong"
