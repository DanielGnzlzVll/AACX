import datetime
import html
import re

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.models import Party, PartyConnection, PartyStatus


def connect(party, user, channel_name, seconds_ago=0):
    return PartyConnection.objects.create(
        party=party,
        user=user,
        channel_name=channel_name,
        last_seen_at=timezone.now() - datetime.timedelta(seconds=seconds_ago),
    )


def card(content, party):
    match = re.search(rf'<a id="party-{party.id}".*?</a>', content, flags=re.DOTALL)
    assert match, f"no card for {party}"
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", match.group())).split())


@pytest.mark.parametrize(
    "fields, expected",
    [
        ({}, PartyStatus.WAITING),
        ({"started_at": timezone.now()}, PartyStatus.PLAYING),
        (
            {"started_at": timezone.now(), "closed_at": timezone.now()},
            PartyStatus.FINISHED,
        ),
    ],
)
def test_party_status(party_factory, fields, expected):
    assert party_factory(**fields).status is expected


def test_available_parties_are_listed_once(party_factory, alice, bob):
    waiting = party_factory(joined_users=[alice, bob])
    playing = party_factory(started_at=timezone.now(), joined_users=[alice, bob])

    parties = list(Party.objects.get_available_parties(alice))

    assert parties == [playing, waiting]


def test_available_parties_count_players(party_factory, alice, bob, user_factory):
    carol = user_factory("carol")
    waiting = party_factory(joined_users=[alice, bob, carol])
    connect(waiting, alice, "alice-1")
    connect(waiting, alice, "alice-2")
    connect(waiting, bob, "bob-1")
    connect(waiting, carol, "carol-1", seconds_ago=3600)
    party_factory(started_at=timezone.now(), joined_users=[alice, bob, carol])

    playing, waiting = Party.objects.get_available_parties(alice)

    assert (waiting.connected_players, waiting.joined_players) == (2, 3)
    assert playing.joined_players == 3


def test_home_cards_show_status_players_and_settings(
    logged_in_client, party_factory, alice, bob
):
    waiting = party_factory(min_players=3, max_rounds=4, max_round_duration=90)
    connect(waiting, bob, "bob-1")
    playing = party_factory(
        started_at=timezone.now(),
        joined_users=[alice, bob],
        max_rounds=6,
        max_round_duration=60,
    )

    content = logged_in_client.get(reverse("home")).content.decode()

    assert card(content, waiting) == (
        f"{waiting.name} Esperando jugadores "
        "1 de 3 jugadores 4 rondas 90 s por ronda Unirse →"
    )
    assert card(content, playing) == (
        f"{playing.name} En curso 2 jugadores 6 rondas 60 s por ronda Continuar →"
    )


def test_home_polls_the_party_list(logged_in_client):
    content = logged_in_client.get(reverse("home")).content.decode()

    (tag,) = re.findall(r'<div id="party_list"[^>]*>', content)
    assert f'hx-get="{reverse("party_list")}"' in tag
    assert 'hx-trigger="every 5s"' in tag
    assert 'hx-swap="outerHTML"' in tag


def test_party_list_requires_login(db):
    url = reverse("party_list")

    response = Client().get(url)

    assert response.status_code == 302
    assert response.url == f"{reverse('login')}?next={url}"


def test_party_list_poll_with_an_expired_session_redirects_the_page(db):
    url = reverse("party_list")

    response = Client().get(url, HTTP_HX_REQUEST="true")

    assert response.status_code == 200
    assert response.headers["HX-Redirect"] == (
        f"{reverse('login')}?next={reverse('home')}"
    )
    assert response.content == b""


def test_party_list_shows_parties_created_after_home_was_loaded(
    logged_in_client, party_factory
):
    logged_in_client.get(reverse("home"))
    party = party_factory(name="new one")

    response = logged_in_client.get(reverse("party_list"), HTTP_HX_REQUEST="true")

    content = response.content.decode()
    assert response.status_code == 200
    assert content.lstrip().startswith('<div id="party_list"')
    assert "<html" not in content
    assert card(content, party).startswith("new one Esperando jugadores")
