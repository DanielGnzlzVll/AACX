import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.core.management.utils import get_random_secret_key

BASE_DIR = Path(__file__).resolve().parent.parent

PRODUCTION_ENV = {
    "DJANGO_SETTINGS_MODULE": "asacx.settings",
    "DJANGO_SECRET_KEY": get_random_secret_key(),
    "DJANGO_ALLOWED_HOSTS": "aacx.example.com",
    "CSRF_TRUSTED_ORIGINS": "https://aacx.example.com",
    "DJANGO_HTTPS": "true",
    "DATABASE_URL": "postgres://user:pass@db.example.com:5432/aacx",
    "REDIS_URL": "redis://redis.example.com:6379/0",
    "REDIS_CACHE_URL": "redis://redis.example.com:6379/1",
}


def isolated_env(env):
    inherited = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("DJANGO_", "DATABASE_", "REDIS_", "LEASE_", "CSRF_"))
    }
    return {**inherited, **env}


def run_django(env, *code):
    return subprocess.run(
        [sys.executable, "-c", "\n".join(("import django", "django.setup()", *code))],
        cwd=BASE_DIR,
        env=isolated_env({"DJANGO_SETTINGS_MODULE": "asacx.settings", **env}),
        capture_output=True,
        text=True,
    )


def test_production_env_passes_deploy_checks():
    result = subprocess.run(
        [sys.executable, "manage.py", "check", "--deploy", "--fail-level", "WARNING"],
        cwd=BASE_DIR,
        env=isolated_env(PRODUCTION_ENV),
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "no issues" in result.stdout


def test_production_env_is_parsed():
    result = run_django(
        PRODUCTION_ENV,
        "from django.conf import settings as s",
        "db = s.DATABASES['default']",
        "print(s.DEBUG, s.ALLOWED_HOSTS, s.CSRF_TRUSTED_ORIGINS)",
        "print(db['HOST'], db['PORT'], db['NAME'], db['USER'])",
        "print([h['address'] for h in s.CHANNEL_LAYERS['default']['CONFIG']['hosts']])",
        "print(s.CACHES['default']['LOCATION'], s.LEASE_REDIS_URL)",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "False ['aacx.example.com'] ['https://aacx.example.com']",
        "db.example.com 5432 aacx user",
        "['redis://redis.example.com:6379/0']",
        "redis://redis.example.com:6379/1 redis://redis.example.com:6379/1",
    ]


def test_secret_key_is_required_without_debug():
    result = run_django({})

    assert result.returncode != 0
    assert "DJANGO_SECRET_KEY" in result.stderr


def test_debug_has_dev_defaults():
    result = run_django(
        {"DJANGO_DEBUG": "true"},
        "from django.conf import settings as s",
        "print(s.DATABASES['default']['HOST'], s.LEASE_REDIS_URL)",
        "print('debug_toolbar' in s.INSTALLED_APPS, s.SECURE_SSL_REDIRECT)",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "db redis://cache:6379/1",
        "True False",
    ]


@pytest.mark.parametrize(
    ("debug", "installed"),
    [("true", True), ("false", False)],
)
def test_debug_toolbar_only_with_debug(debug, installed):
    result = run_django(
        {"DJANGO_DEBUG": debug, "DJANGO_SECRET_KEY": PRODUCTION_ENV["DJANGO_SECRET_KEY"]},
        "from django.conf import settings as s",
        "from django.urls import resolve, Resolver404",
        "try:",
        "    resolve('/__debug__/render_panel/')",
        "    routed = True",
        "except Resolver404:",
        "    routed = False",
        "print('debug_toolbar' in s.INSTALLED_APPS, 'django_extensions' in s.INSTALLED_APPS, routed)",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == [str(installed)] * 3


def test_asgi_application_loads_without_settings_module():
    result = subprocess.run(
        [sys.executable, "-c", "from asacx.asgi import application"],
        cwd=BASE_DIR,
        env=isolated_env({"DJANGO_SECRET_KEY": PRODUCTION_ENV["DJANGO_SECRET_KEY"]}),
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
