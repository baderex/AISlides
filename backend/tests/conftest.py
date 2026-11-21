import os
import tempfile

import pytest

scratch = tempfile.TemporaryDirectory(prefix="framecraft-test-")
os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + scratch.name.replace("\\", "/") + "/database.db"
os.environ["LOCAL_STORAGE_PATH"] = scratch.name + "/uploads"
os.environ["STORAGE_BACKEND"] = "local"
os.environ["JOB_BACKEND"] = "local"
os.environ["REDIS_ENABLED"] = "false"
os.environ["LOCAL_SESSION"] = "true"
os.environ["ALLOW_SIGNUP"] = "true"

from app.auth import LOCAL_USER
from app.db import Base, Session, User, engine
from app.skills import seed


@pytest.fixture(scope="session", autouse=True)
def close_database():
    yield
    engine.dispose()
    scratch.cleanup()


@pytest.fixture(autouse=True)
def database():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Session() as db:
        db.add(User(id=LOCAL_USER, email="local@framecraft.test"))
        seed(db)
    yield
