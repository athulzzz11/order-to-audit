import pytest

from app import db as dbm
from app.seed import seed


@pytest.fixture
def db():
    dbm.init("sqlite://")
    dbm.drop_all()
    dbm.create_all()
    s = dbm.SessionLocal()
    seed(s)
    yield s
    s.close()
