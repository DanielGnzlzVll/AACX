import datetime
import re

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import PartyRound

TIMER = re.compile(
    r'<p class="round-countdown( is-warning)?" role="timer" '
    r'data-seconds-left="(\d+)">\2 s</p>'
)


def in_seconds(seconds):
    return timezone.now() + datetime.timedelta(seconds=seconds)


def timers(html):
    return [(int(seconds), bool(warning)) for warning, seconds in TIMER.findall(html)]


def get_party_page(client, party):
    response = client.get(reverse("detail_party", kwargs={"party_id": party.id}))
    assert response.status_code == 200
    return response.content.decode()


@pytest.fixture
def started_party(party_factory, alice):
    return party_factory(started_at=timezone.now(), joined_users=[alice])


def test_a_new_round_ends_after_the_party_round_duration(party_factory):
    party = party_factory(started_at=timezone.now(), max_round_duration=90)

    round = party._get_current_or_next_round()

    assert round.deadline_at == round.started_at + datetime.timedelta(seconds=90)


@pytest.mark.parametrize(
    "deadline_in, closed, expected",
    [(45, False, 45), (0.2, False, 1), (-5, False, 0), (45, True, 0)],
)
def test_seconds_left(started_party, deadline_in, closed, expected):
    round = PartyRound(
        party=started_party,
        letter="A",
        deadline_at=in_seconds(deadline_in),
        closed_at=timezone.now() if closed else None,
    )

    assert round.seconds_left == expected


@pytest.mark.parametrize(
    "deadline_in, expected", [(45, (45, False)), (10, (10, False)), (9, (9, True))]
)
def test_party_page_counts_down_from_the_round_deadline(
    logged_in_client, started_party, deadline_in, expected
):
    PartyRound.objects.create(
        party=started_party, letter="A", deadline_at=in_seconds(deadline_in)
    )

    assert timers(get_party_page(logged_in_client, started_party)) == [expected]


def test_party_page_shows_zero_once_the_round_closed(logged_in_client, started_party):
    PartyRound.objects.create(
        party=started_party,
        letter="A",
        deadline_at=in_seconds(45),
        closed_at=timezone.now(),
    )

    assert timers(get_party_page(logged_in_client, started_party)) == [(0, True)]


def test_party_page_has_no_countdown_before_the_first_round(
    logged_in_client, started_party
):
    html = get_party_page(logged_in_client, started_party)

    assert 'id="round_countdown"' in html
    assert timers(html) == []


def test_finished_party_has_no_countdown(logged_in_client, started_party):
    PartyRound.objects.create(
        party=started_party, letter="A", closed_at=timezone.now()
    )
    started_party.closed_at = timezone.now()
    started_party.save()

    assert timers(get_party_page(logged_in_client, started_party)) == []


@pytest.mark.django_db(transaction=True)
async def test_a_closed_round_stops_every_player_countdown(
    ws_connect, channel_layer, started_party, alice
):
    round = await PartyRound.objects.acreate(party=started_party, letter="A")
    communicator = await ws_connect(alice, f"/party/{started_party.id}/")
    await round.close()

    await channel_layer.group_send(
        f"party_{started_party.id}", {"type": "event_party_round_stopped"}
    )

    message = await communicator.receive_from()
    assert 'id="round_countdown"' in message
    assert timers(message) == [(0, True)]
