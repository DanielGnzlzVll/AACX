import asyncio

import pytest
from django.utils import timezone

from core import consumers, models

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def party(party_factory, alice):
    return party_factory(started_at=timezone.now(), joined_users=[alice])


@pytest.fixture
def party_round(party):
    return models.PartyRound.objects.create(
        party=party, letter="A", started_at=timezone.now()
    )


@pytest.fixture
async def probe(channel_layer, party):
    await channel_layer.group_add(f"party_{party.id}", "probe")
    return "probe"


@pytest.fixture
def new_round_channel(party):
    return f"party_new_round_{party.id}"


@pytest.fixture
def stop_event(party, party_round):
    return {
        "type": "event_party_round_stopped",
        "party_id": party.id,
        "round_id": party_round.id,
    }


async def test_stop_button_sends_only_primitives_to_state_machine(
    ws_connect, receive_or_none, alice, party, stop_event
):
    communicator = await ws_connect(alice, f"/party/{party.id}/")
    started = await receive_or_none(consumers.STATE_MACHINE_CHANNEL_NAME)
    assert started["type"] == "event_party_started"

    await communicator.send_json_to(
        {
            "HEADERS": {"HX-Trigger": "party_current_answers_form"},
            "name": "Ana",
            "submit_stop": "on",
        }
    )

    stopped = await receive_or_none(consumers.STATE_MACHINE_CHANNEL_NAME, timeout=2)
    assert stopped == stop_event
    assert await communicator.receive_nothing()


async def test_stop_closes_round_and_notifies_players(
    state_machine, receive_or_none, party_round, probe, new_round_channel, stop_event
):
    await state_machine.event_party_round_stopped(stop_event)

    await party_round.arefresh_from_db()
    assert party_round.closed_at is not None
    notification = await receive_or_none(probe)
    assert notification["type"] == "event_party_round_stopped"
    assert await receive_or_none(new_round_channel) == {"round_id": party_round.id}


async def test_close_updates_the_instance_only_once(party_round):
    assert await party_round.close()
    assert party_round.closed_at is not None
    assert not await party_round.close()


async def test_duplicate_stops_end_the_round_once(
    state_machine, receive_or_none, probe, new_round_channel, stop_event
):
    await asyncio.gather(
        state_machine.event_party_round_stopped(stop_event),
        state_machine.event_party_round_stopped(stop_event),
    )

    assert await receive_or_none(probe) is not None
    assert await receive_or_none(probe) is None
    assert await receive_or_none(new_round_channel) is not None
    assert await receive_or_none(new_round_channel) is None


async def test_stop_for_closed_round_is_ignored(
    state_machine, receive_or_none, party_round, probe, new_round_channel, stop_event
):
    party_round.closed_at = timezone.now()
    await party_round.asave()

    await state_machine.event_party_round_stopped(stop_event)

    assert await receive_or_none(probe) is None
    assert await receive_or_none(new_round_channel) is None


async def test_stale_round_end_does_not_end_the_current_round(
    state_machine, channel_layer, party, party_round, new_round_channel
):
    party.max_round_duration = 60
    await channel_layer.send(new_round_channel, {"round_id": party_round.id - 1})

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(
            state_machine.wait_for_round_end(party, party_round), timeout=0.3
        )

    await channel_layer.send(new_round_channel, {"round_id": party_round.id})
    await asyncio.wait_for(
        state_machine.wait_for_round_end(party, party_round), timeout=1
    )


async def test_round_timeout_closes_round_and_notifies_players(
    state_machine, receive_or_none, party, party_round, probe
):
    party.max_round_duration = 0

    await state_machine.wait_for_round_end(party, party_round)

    await party_round.arefresh_from_db()
    assert party_round.closed_at is not None
    notification = await receive_or_none(probe)
    assert notification["type"] == "event_party_round_stopped"


async def test_scores_the_stopped_round_without_creating_a_new_one(
    state_machine, instant_reveal, alice, party, party_round, stop_event
):
    await models.UserRoundAnswer.objects.acreate(
        round=party_round, user=alice, field="name", value="Ana"
    )
    await state_machine.event_party_round_stopped(stop_event)

    await state_machine.update_scores(party, party_round)

    assert await models.PartyRound.objects.filter(party=party).acount() == 1
    answer = await models.UserRoundAnswer.objects.aget(round=party_round)
    assert answer.scored_points == 100
