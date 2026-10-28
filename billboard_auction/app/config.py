import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'billboard.db'}")
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")
TOKEN_TTL_SECONDS = 7 * 24 * 3600

UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", BASE_DIR / "uploads"))
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
CREATIVE_DEADLINE_DAYS = 3

CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", CELERY_BROKER_URL)

# Schedule a Celery ETA task for every auction deadline (the beat sweeper is
# always the safety net). Off by default so the app runs without Redis.
SCHEDULE_WITH_CELERY = os.getenv("SCHEDULE_WITH_CELERY", "0") == "1"
# Run a small in-process sweeper thread. Handy for local demos; turn it off
# when a real Celery worker + beat is running.
EMBEDDED_SWEEPER = os.getenv("EMBEDDED_SWEEPER", "1") == "1"
SWEEP_INTERVAL_SECONDS = int(os.getenv("SWEEP_INTERVAL_SECONDS", "5"))

DEFAULT_ANTI_SNIPE_SECONDS = 120
CURRENCY = "INR"
