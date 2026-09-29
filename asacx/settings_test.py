import os

from asacx.settings import *  # noqa: F401,F403
from asacx.settings import DATABASES

DATABASES["default"]["HOST"] = os.environ.get("POSTGRES_HOST", "db")
DATABASES["default"]["PORT"] = int(os.environ.get("POSTGRES_PORT", 5432))

CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}

LEASE_REDIS_URL = "redis://{}:{}/1".format(
    os.environ.get("REDIS_HOST", "cache"), os.environ.get("REDIS_PORT", 6379)
)

CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

STORAGES = {
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
