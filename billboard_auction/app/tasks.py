"""Celery workers: automatic auction expiry.

Run with:
    celery -A app.tasks worker -l info
    celery -A app.tasks beat   -l info

Two layers close auctions:
  1. close_auction_task, queued with eta=end_at when an auction is created.
     If anti-sniping pushed the deadline out, it re-queues itself.
  2. sweep_due_auctions, run by beat every 10 seconds, which catches anything
     the first layer missed (broker restarts, lost messages, workers down).
Both call services.close_auction, which locks the row and is idempotent, so
running them twice or on several workers at once is harmless.
"""
import logging

from celery import Celery

from . import config
from .database import SessionLocal, init_db
from .models import Auction, as_utc
from .services import close_auction, close_due

log = logging.getLogger("billboard.tasks")

celery_app = Celery("billboard", broker=config.CELERY_BROKER_URL, backend=config.CELERY_RESULT_BACKEND)
celery_app.conf.update(
    timezone="UTC",
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    beat_schedule={
        "sweep-due-auctions": {"task": "billboard.sweep_due_auctions", "schedule": 10.0},
    },
)


@celery_app.task(name="billboard.close_auction", autoretry_for=(Exception,), retry_backoff=True, max_retries=5)
def close_auction_task(auction_id: int) -> str:
    init_db()
    with SessionLocal() as db:
        result = close_auction(db, auction_id)
        if result == "not_due":
            # Deadline moved (anti-sniping). Try again at the new deadline.
            end_at = as_utc(db.get(Auction, auction_id).end_at)
            close_auction_task.apply_async(args=[auction_id], eta=end_at)
            return "rescheduled"
        log.info("auction %s -> %s", auction_id, result)
        return result


@celery_app.task(name="billboard.sweep_due_auctions")
def sweep_due_auctions() -> list:
    init_db()
    with SessionLocal() as db:
        done = close_due(db)
        if done:
            log.info("swept %s", done)
        return done
