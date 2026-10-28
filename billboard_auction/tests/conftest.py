import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Must be set before the app is imported. Set TEST_DATABASE_URL to a PostgreSQL
# URL to also run the concurrency test against a real database.
os.environ["DATABASE_URL"] = os.getenv("TEST_DATABASE_URL", "sqlite:///:memory:")
os.environ["EMBEDDED_SWEEPER"] = "0"
os.environ["SCHEDULE_WITH_CELERY"] = "0"
os.environ["UPLOAD_DIR"] = tempfile.mkdtemp(prefix="bidboard-uploads-")

import pytest  # noqa: E402

from app.database import Base, SessionLocal, engine  # noqa: E402
from app.models import User  # noqa: E402
from app.security import hash_password  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


def make_user(db, email, role="advertiser", name=None):
    u = User(email=email, name=name or email.split("@")[0], company=None, role=role,
             password_hash=hash_password("password123"))
    db.add(u)
    db.commit()
    return u


@pytest.fixture
def owner(db):
    return make_user(db, "owner@test.dev", "owner")


@pytest.fixture
def ads(db):
    return [make_user(db, f"ad{i}@test.dev", "advertiser") for i in range(3)]
