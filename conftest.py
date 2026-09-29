import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def clear_cache():
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def keep_test_db_connection(monkeypatch):
    # Channels closes old DB connections before every consumer handler, which
    # would close the connection a TestCase keeps open for its transaction.
    monkeypatch.setattr("channels.db.close_old_connections", lambda: None)
