import asyncio
import datetime
import json

import pytest
from asgiref.sync import sync_to_async
from django.utils import timezone

from core import consumers, models

pytestmark = pytest.mark.django_db(transaction=True)

ANSWERS_FORM = 'id="party_current_answers_form"'
NAME_STATUS = 'id="answer_error_name"'


def answers_message(trigger="party_current_answers_form", **answers):
    return json.dumps({"HEADERS": {"HX-Trigger": trigger}, **answers})


async def saved_answers(user):
    answers = models.UserRoundAnswer.objects.filter(user=user)
    return {answer.field: answer.value async for answer in answers}


@pytest.fixture
def started_party(party_factory, alice):
    party = party_factory(started_at=timezone.now(), joined_users=[alice])
    models.PartyRound.objects.create(party=party, letter="A")
    return party


async def connect(ws_communicator, user, party_id):
    communicator = await ws_communicator(user, f"/party/{party_id}/")
    connected, code = await communicator.connect()
    return communicator, connected, code


async def assert_still_open(communicator):
    assert await communicator.receive_nothing(timeout=0.5)
    assert not communicator.future.done()


async def test_anonymous_user_is_rejected(ws_communicator, party_factory):
    party = await sync_to_async(party_factory)()

    _, connected, code = await connect(ws_communicator, None, party.id)

    assert (connected, code) == (False, 4401)


async def test_missing_party_is_rejected(ws_communicator, alice):
    _, connected, code = await connect(ws_communicator, alice, 0)

    assert (connected, code) == (False, 4404)


async def test_non_participant_is_rejected_from_started_party(
    ws_communicator, started_party, bob
):
    _, connected, code = await connect(ws_communicator, bob, started_party.id)

    assert (connected, code) == (False, 4403)


async def test_non_participant_is_rejected_from_finished_party(
    ws_communicator, party_factory, alice, bob
):
    party = await sync_to_async(party_factory)(
        started_at=timezone.now(), closed_at=timezone.now(), joined_users=[alice]
    )

    _, connected, code = await connect(ws_communicator, bob, party.id)

    assert (connected, code) == (False, 4403)


async def test_participant_can_reconnect_to_started_party(
    ws_communicator, started_party, alice
):
    communicator, connected, _ = await connect(
        ws_communicator, alice, started_party.id
    )

    assert connected
    await assert_still_open(communicator)


async def test_any_user_can_join_waiting_party(
    ws_communicator, channel_layer, party_factory, bob
):
    party = await sync_to_async(party_factory)()

    communicator, connected, _ = await connect(ws_communicator, bob, party.id)

    assert connected
    await assert_still_open(communicator)
    player = await channel_layer.receive(f"party_players_{party.id}")
    assert player["user_id"] == bob.id
    assert "hola" not in player
    started = await channel_layer.receive(consumers.STATE_MACHINE_CHANNEL_NAME)
    assert started["party_id"] == party.id


@pytest.mark.parametrize(
    "frame",
    [
        {"text_data": "not json"},
        {"text_data": "[]"},
        {"text_data": "{}"},
        {"text_data": json.dumps({"HEADERS": "party_current_answers_form"})},
        {"text_data": json.dumps({"HEADERS": {}})},
        {"text_data": json.dumps({"HEADERS": {"HX-Trigger": "unknown"}})},
        {"bytes_data": b"\x00\x01"},
    ],
)
async def test_malformed_messages_are_ignored(
    ws_communicator, started_party, alice, frame
):
    communicator, _, _ = await connect(ws_communicator, alice, started_party.id)

    await communicator.send_to(**frame)

    await assert_still_open(communicator)
    await communicator.send_to(text_data=answers_message(name="Ana"))
    assert NAME_STATUS in await communicator.receive_from()


async def test_answers_before_first_round_are_ignored(
    ws_communicator, party_factory, alice
):
    party = await sync_to_async(party_factory)(joined_users=[alice])
    communicator, _, _ = await connect(ws_communicator, alice, party.id)

    await communicator.send_to(text_data=answers_message(name="Ana"))

    await assert_still_open(communicator)


async def test_user_connected_while_waiting_plays_after_start(
    ws_communicator, channel_layer, party_factory, alice, bob
):
    party = await sync_to_async(party_factory)(joined_users=[alice])
    communicator, _, _ = await connect(ws_communicator, bob, party.id)
    await channel_layer.receive(consumers.STATE_MACHINE_CHANNEL_NAME)
    party.started_at = timezone.now()
    await party.asave()
    await models.PartyRound.objects.acreate(party=party, letter="A")

    await communicator.send_to(text_data=answers_message(name="Ana"))

    assert NAME_STATUS in await communicator.receive_from()
    assert await models.UserRoundAnswer.objects.filter(user=bob).aexists()


async def test_connecting_to_waiting_party_records_participation_and_presence(
    ws_communicator, channel_layer, party_factory, alice
):
    party = await sync_to_async(party_factory)()

    for _ in range(2):
        await connect(ws_communicator, alice, party.id)
        await channel_layer.receive(f"party_players_{party.id}")

    assert [user async for user in party.joined_users.all()] == [alice]
    assert await models.PartyConnection.objects.filter(
        party=party, user=alice
    ).acount() == 2
    assert await party.acount_connected_players() == 1


async def test_connecting_to_started_party_does_not_track_presence(
    ws_communicator, channel_layer, started_party, alice
):
    await connect(ws_communicator, alice, started_party.id)
    await channel_layer.receive(consumers.STATE_MACHINE_CHANNEL_NAME)

    assert not await models.PartyConnection.objects.aexists()
    assert f"party_players_{started_party.id}" not in channel_layer.channels


async def test_disconnect_leaves_group_and_presence(
    ws_communicator, channel_layer, party_factory, alice
):
    party = await sync_to_async(party_factory)()
    communicator, _, _ = await connect(ws_communicator, alice, party.id)
    await channel_layer.receive(f"party_players_{party.id}")

    await communicator.disconnect()

    assert not channel_layer.groups.get(f"party_{party.id}")
    assert not await models.PartyConnection.objects.aexists()
    notification = await channel_layer.receive(f"party_players_{party.id}")
    assert notification["user_id"] == alice.id


async def test_presence_is_refreshed_while_connected(
    ws_communicator, channel_layer, party_factory, alice, monkeypatch
):
    monkeypatch.setattr(
        models.PartyConnection, "HEARTBEAT_INTERVAL", datetime.timedelta(0)
    )
    party = await sync_to_async(party_factory)()
    await connect(ws_communicator, alice, party.id)
    await channel_layer.receive(f"party_players_{party.id}")
    long_ago = timezone.now() - 2 * models.PartyConnection.TTL
    await models.PartyConnection.objects.aupdate(last_seen_at=long_ago)

    async with asyncio.timeout(2):
        while not await party.acount_connected_players():
            await asyncio.sleep(0.05)


async def test_answer_longer_than_model_field_is_rejected(
    ws_communicator, started_party, alice
):
    communicator, _, _ = await connect(ws_communicator, alice, started_party.id)

    await communicator.send_to(text_data=answers_message(name="A" * 51, city="Arica"))

    assert "word-error" in await communicator.receive_from()
    await assert_still_open(communicator)
    answers = models.UserRoundAnswer.objects.filter(user=alice).exclude(value="")
    assert {a.field: a.value async for a in answers} == {"city": "Arica"}


async def test_heartbeat_requests_the_start_until_the_party_starts(
    ws_communicator, channel_layer, party_factory, alice, monkeypatch
):
    monkeypatch.setattr(
        models.PartyConnection, "HEARTBEAT_INTERVAL", datetime.timedelta(seconds=0.05)
    )
    party = await sync_to_async(party_factory)()
    await connect(ws_communicator, alice, party.id)
    await channel_layer.receive(consumers.STATE_MACHINE_CHANNEL_NAME)

    async with asyncio.timeout(2):
        started = await channel_layer.receive(consumers.STATE_MACHINE_CHANNEL_NAME)
    assert started["party_id"] == party.id

    await models.Party.objects.filter(id=party.id).aupdate(started_at=timezone.now())
    await asyncio.sleep(0.2)
    channel_layer.channels.pop(consumers.STATE_MACHINE_CHANNEL_NAME, None)
    await asyncio.sleep(0.2)
    assert consumers.STATE_MACHINE_CHANNEL_NAME not in channel_layer.channels


async def test_autosave_reply_does_not_replace_inputs(
    ws_communicator, started_party, alice
):
    communicator, _, _ = await connect(ws_communicator, alice, started_party.id)

    await communicator.send_to(text_data=answers_message(name="Ana", city="Bogota"))

    reply = await communicator.receive_from()
    assert NAME_STATUS in reply
    assert 'id="answer_error_city"' in reply
    assert "word-error" in reply
    for replaced in ("<input", "<form", "<script", ANSWERS_FORM):
        assert replaced not in reply


@pytest.mark.parametrize("value", ["", "A"])
async def test_shortened_answer_replaces_saved_answer(
    ws_communicator, started_party, alice, value
):
    communicator, _, _ = await connect(ws_communicator, alice, started_party.id)
    await communicator.send_to(text_data=answers_message(name="Ana"))
    await communicator.receive_from()

    await communicator.send_to(text_data=answers_message(name=value))
    await communicator.receive_from()

    assert (await saved_answers(alice))["name"] == value


async def test_too_long_answer_clears_saved_answer(
    ws_communicator, started_party, alice
):
    communicator, _, _ = await connect(ws_communicator, alice, started_party.id)
    await communicator.send_to(text_data=answers_message(name="Ana"))
    await communicator.receive_from()

    await communicator.send_to(text_data=answers_message(name="A" * 51))

    assert "word-error" in await communicator.receive_from()
    assert (await saved_answers(alice))["name"] == ""


async def test_stop_button_saves_answers_and_stops_round(
    ws_communicator, channel_layer, started_party, alice
):
    communicator, _, _ = await connect(ws_communicator, alice, started_party.id)
    await channel_layer.receive(consumers.STATE_MACHINE_CHANNEL_NAME)

    await communicator.send_to(
        text_data=answers_message("submit_stop", name="Ana", submit_stop="on")
    )

    stopped = await asyncio.wait_for(
        channel_layer.receive(consumers.STATE_MACHINE_CHANNEL_NAME), timeout=2
    )
    round = await started_party.aget_current_round()
    assert stopped == {
        "type": "event_party_round_stopped",
        "party_id": started_party.id,
        "round_id": round.id,
    }
    assert (await saved_answers(alice))["name"] == "Ana"
