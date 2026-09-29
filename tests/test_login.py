import time
from unittest import mock

import pytest
from django.contrib.auth import SESSION_KEY
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from pytest_django.asserts import assertContains, assertRedirects, assertTemplateUsed

from core.views import (
    LOGIN_RATE_LIMITED_MESSAGE,
    LOGIN_REJECTED_MESSAGE,
    NICKNAME_CLAIM_COOKIE,
    NICKNAME_CLAIM_LIMIT,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def login(client):
    def post(nickname=None, url=None, **extra):
        data = {} if nickname is None else {"nickname": nickname}
        return client.post(url or reverse("login"), data, **extra)

    return post


@pytest.fixture
def logout(client):
    def post():
        return client.post(reverse("logout"))

    return post


def create_nickname(nickname, **extra):
    return Client().post(reverse("login"), {"nickname": nickname}, **extra)


def assert_logged_out(client):
    assert SESSION_KEY not in client.session


def assert_logged_in_as(client, username):
    assert int(client.session[SESSION_KEY]) == User.objects.get(username=username).pk


def assert_rejected(response):
    assert response.status_code == 200
    assertTemplateUsed(response, "login.html")
    assertContains(response, LOGIN_REJECTED_MESSAGE)
    assert_logged_out(response.client)


def assert_rate_limited(response, nickname):
    assert response.status_code == 429
    assertTemplateUsed(response, "login.html")
    assertContains(response, LOGIN_RATE_LIMITED_MESSAGE, status_code=429)
    assert_logged_out(response.client)
    assert not User.objects.filter(username=nickname).exists()


def test_get_renders_form(client):
    response = client.get(reverse("login"))

    assert response.status_code == 200
    assertContains(response, 'name="nickname"')


def test_new_nickname_creates_passwordless_user_and_logs_in(client, login):
    response = login("  ana  ")

    assertRedirects(response, reverse("home"))
    user = User.objects.get(username="ana")
    assert not user.has_usable_password()
    assert_logged_in_as(client, "ana")
    assert NICKNAME_CLAIM_COOKIE in response.cookies


def test_superuser_username_is_rejected(client, login):
    User.objects.create_superuser("root", "root@example.com", "s3cret-pass")

    assert_rejected(login("root"))
    assertRedirects(
        client.get("/admin/"),
        "/admin/login/?next=/admin/",
        fetch_redirect_response=False,
    )


def test_superuser_username_with_different_case_is_rejected(login):
    User.objects.create_superuser("root", "root@example.com", "s3cret-pass")

    assert_rejected(login("ROOT"))
    assert not User.objects.filter(username="ROOT").exists()


def test_staff_username_is_rejected(login):
    user = User(username="staffer", is_staff=True)
    user.set_unusable_password()
    user.save()

    assert_rejected(login("staffer"))


def test_user_with_password_is_rejected(login):
    User.objects.create_user("carla", password="s3cret-pass")

    assert_rejected(login("carla"))


def test_nickname_claimed_by_another_browser_is_rejected(login):
    create_nickname("ana")

    assert_rejected(login("ana"))


def test_nickname_with_tampered_claim_cookie_is_rejected(client, login, logout):
    login("ana")
    logout()
    other = User(username="bob")
    other.set_unusable_password()
    other.save()
    client.cookies[NICKNAME_CLAIM_COOKIE] = str(other.pk)

    assert_rejected(login("bob"))


def test_same_browser_can_log_back_in_with_its_nickname(client, login, logout):
    login("ana")
    logout()

    response = login("ana")

    assertRedirects(response, reverse("home"))
    assert User.objects.filter(username="ana").count() == 1
    assert_logged_in_as(client, "ana")


def test_inactive_user_with_claim_is_rejected(login, logout):
    login("ana")
    logout()
    User.objects.filter(username="ana").update(is_active=False)

    assert_rejected(login("ana"))


def test_browser_keeps_claims_on_every_nickname_it_used(client, login, logout):
    login("ana")
    logout()
    login("bob")
    logout()

    for nickname in ("ana", "bob"):
        assertRedirects(login(nickname), reverse("home"))
        assert_logged_in_as(client, nickname)
        logout()


def test_claim_cookie_keeps_only_the_most_recent_nicknames(login, logout):
    nicknames = [f"player{i}" for i in range(NICKNAME_CLAIM_LIMIT + 1)]
    for nickname in nicknames:
        login(nickname)
        logout()

    assert_rejected(login(nicknames[0]))
    assertRedirects(login(nicknames[1]), reverse("home"))


def test_claim_cookie_is_secure_only_over_https(login):
    plain = login("ana")
    secure = create_nickname("bob", secure=True)

    assert not plain.cookies[NICKNAME_CLAIM_COOKIE]["secure"]
    assert secure.cookies[NICKNAME_CLAIM_COOKIE]["secure"]


@pytest.mark.parametrize(
    "nickname",
    [
        pytest.param(None, id="missing"),
        pytest.param("", id="empty"),
        pytest.param("   ", id="blank"),
        pytest.param("ab", id="too short"),
        pytest.param("a" * 31, id="too long"),
        pytest.param("<script>", id="invalid characters"),
        pytest.param("ana maria", id="spaces inside"),
        pytest.param("\u0430na", id="cyrillic homoglyph"),
        pytest.param("josé", id="accented letter"),
    ],
)
def test_invalid_nicknames_rerender_form_with_errors(client, login, nickname):
    response = login(nickname)

    assert response.status_code == 200
    assertTemplateUsed(response, "login.html")
    assert response.context["form"].errors
    assert_logged_out(client)
    assert not User.objects.exists()


def test_redirects_to_safe_next_url(login):
    response = login("ana", url=f"{reverse('login')}?next=/party/create/")

    assertRedirects(response, "/party/create/", fetch_redirect_response=False)


def test_ignores_external_next_url(login):
    response = login("ana", url=f"{reverse('login')}?next=https://evil.example/")

    assertRedirects(response, reverse("home"))


def test_nickname_creation_over_the_limit_is_rejected(settings):
    settings.LOGIN_NICKNAME_CREATION_LIMIT = 2
    for nickname in ("ana", "bob"):
        assertRedirects(create_nickname(nickname), reverse("home"))

    assert_rate_limited(create_nickname("carla"), "carla")


def test_logging_back_in_does_not_count_against_the_limit(settings, login, logout):
    settings.LOGIN_NICKNAME_CREATION_LIMIT = 2
    login("ana")
    for _ in range(3):
        logout()
        assertRedirects(login("ana"), reverse("home"))

    assertRedirects(create_nickname("bob"), reverse("home"))


def test_rejected_nickname_does_not_count_against_the_limit(settings, login):
    settings.LOGIN_NICKNAME_CREATION_LIMIT = 1
    User.objects.create_user("carla", password="s3cret-pass")

    assert_rejected(login("carla"))
    assertRedirects(create_nickname("ana"), reverse("home"))


def test_nickname_creation_limit_is_per_ip(settings):
    settings.LOGIN_NICKNAME_CREATION_LIMIT = 1
    create_nickname("ana", REMOTE_ADDR="192.0.2.1")

    assertRedirects(create_nickname("bob", REMOTE_ADDR="192.0.2.2"), reverse("home"))
    assert_rate_limited(create_nickname("carla", REMOTE_ADDR="192.0.2.1"), "carla")


def test_nickname_creation_limit_resets_after_the_window(settings):
    settings.LOGIN_NICKNAME_CREATION_LIMIT = 1
    settings.LOGIN_NICKNAME_CREATION_WINDOW = 60
    create_nickname("ana")
    assert_rate_limited(create_nickname("bob"), "bob")

    later = time.time() + 61
    with mock.patch("django.core.cache.backends.locmem.time") as locmem_time:
        locmem_time.time.return_value = later
        response = create_nickname("bob")

    assertRedirects(response, reverse("home"))


def test_client_ip_comes_from_the_trusted_forwarded_header(settings):
    settings.LOGIN_NICKNAME_CREATION_LIMIT = 1
    settings.CLIENT_IP_HEADER = "HTTP_X_FORWARDED_FOR"
    proxy = {"REMOTE_ADDR": "10.0.0.1"}
    create_nickname("ana", HTTP_X_FORWARDED_FOR="192.0.2.1", **proxy)

    assertRedirects(
        create_nickname("bob", HTTP_X_FORWARDED_FOR="192.0.2.2", **proxy),
        reverse("home"),
    )
    assert_rate_limited(
        create_nickname(
            "carla", HTTP_X_FORWARDED_FOR="198.51.100.7, 192.0.2.1", **proxy
        ),
        "carla",
    )


def test_forwarded_header_is_ignored_unless_trusted(settings):
    settings.LOGIN_NICKNAME_CREATION_LIMIT = 1
    create_nickname("ana", HTTP_X_FORWARDED_FOR="192.0.2.1")

    assert_rate_limited(create_nickname("bob", HTTP_X_FORWARDED_FOR="192.0.2.2"), "bob")


@pytest.fixture
def logged_in(login):
    login("ana")


def test_navbar_links_to_logout(client, logged_in):
    response = client.get(reverse("home"))

    assertContains(response, f'action="{reverse("logout")}"')


def test_logout_ends_session_and_redirects_to_login(client, logged_in, logout):
    response = logout()

    assertRedirects(response, reverse("login"))
    assert_logged_out(client)


def test_logout_rejects_get(client, logged_in):
    response = client.get(reverse("logout"))

    assert response.status_code == 405
    assert SESSION_KEY in client.session
