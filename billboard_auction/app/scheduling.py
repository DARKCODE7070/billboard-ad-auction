"""Deadline scheduling hook.

Services call `schedule_close` when an auction is created. With Celery enabled
this queues an ETA task for the exact deadline. If the broker is unreachable
we only log: the periodic sweeper (Celery beat or the embedded thread) always
closes anything overdue, so a missed ETA task delays settlement by seconds,
never forever.
"""
import logging
from datetime import datetime

from . import config

log = logging.getLogger("billboard.scheduling")


def schedule_close(auction_id: int, end_at: datetime) -> None:
    if not config.SCHEDULE_WITH_CELERY:
        return
    try:
        from .tasks import close_auction_task

        close_auction_task.apply_async(args=[auction_id], eta=end_at)
    except Exception as exc:  # broker down, etc.
        log.warning("Could not queue ETA close for auction %s: %s", auction_id, exc)
