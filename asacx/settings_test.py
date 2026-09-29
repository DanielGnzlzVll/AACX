import os

os.environ.setdefault("DJANGO_SECRET_KEY", "django-insecure-test-only")

from asacx.settings import *  # noqa: E402,F401,F403
from asacx.settings import DATABASES  # noqa: E402

DATABASES["default"]["HOST"] = os.environ.get("POSTGRES_HOST", "db")
DATABASES["default"]["PORT"] = int(os.environ.get("POSTGRES_PORT", 5432))

CHANNEL_LAYERS = {"default": {"BACKEND": "core.testing.MsgpackInMemoryChannelLayer"}}

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

ANSWER_VALIDATORS = []
