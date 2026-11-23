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
from model_fixture import model_fixture


@pytest.fixture(autouse=True)
def use_model_fixture():
    with model_fixture():
        yield


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


@pytest.fixture(autouse=True)
def artifact_worker(monkeypatch):
    # Exercise the same claim and completion paths without starting a service per API test.
    from app.artifacts import claim_artifact, execute

    def dispatch(_):
        with Session() as db:
            ticket = claim_artifact(db, "test-worker")
        if ticket:
            execute(*ticket)

    monkeypatch.setattr("app.artifacts.notify", dispatch)
