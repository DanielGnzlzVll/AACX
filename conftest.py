import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def clear_cache():
    yield
    cache.clear()
