import re

import pytest
from django.urls import reverse

SCRIPT_SRC = re.compile(r"<script[^>]*\bsrc=\"([^\"]+)\"")
EXTERNAL_URL = re.compile(r"\b(?:src|href)=\"((?:https?:)?//[^\"]+)\"")


@pytest.fixture
def home_html(logged_in_client):
    def render():
        response = logged_in_client.get(reverse("home"))
        assert response.status_code == 200
        return response.content.decode()

    return render


def test_scripts_are_served_locally(home_html, settings):
    settings.DEBUG = True

    sources = SCRIPT_SRC.findall(home_html())

    assert sources
    assert all(src.startswith(settings.STATIC_URL) for src in sources)


def test_debug_extension_absent_without_debug(home_html, settings):
    settings.DEBUG = False

    html = home_html()

    assert "htmx-ext-debug" not in html
    assert 'hx-ext="debug"' not in html


def test_debug_extension_enabled_with_debug(home_html, settings):
    settings.DEBUG = True

    html = home_html()

    assert "htmx-ext-debug" in html
    assert 'hx-ext="debug"' in html


def test_username_is_not_sent_to_third_parties(home_html, alice):
    external_urls = EXTERNAL_URL.findall(home_html())

    assert not [url for url in external_urls if alice.username in url]
    assert "pravatar" not in home_html()


def test_avatar_shows_username_initial(home_html):
    assert re.search(r'class="avatar"[^>]*>\s*A\s*<', home_html())
