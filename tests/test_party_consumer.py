import json

import pytest
from asgiref.sync import sync_to_async
from django.utils import timezone

from core import consumers, models

pytestmark = pytest.mark.django_db(transaction=True)

ANSWERS_FORM = 'id="party_current_answers_form"'


def answers_message(**answers):
    return json.dumps(
        {"HEADERS": {"HX-Trigger": "party_current_answers_form"}, **answers}
    )


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
    assert ANSWERS_FORM in await communicator.receive_from()


async def test_answers_before_first_round_are_ignored(
    ws_communicator, party_factory, alice
):
    party = await sync_to_async(party_factory)(joined_users=[alice])
    communicator, _, _ = await connect(ws_communicator, alice, party.id)

    await communicator.send_to(text_data=answers_message(name="Ana"))

    await assert_still_open(communicator)


async def test_non_participant_connected_before_start_cannot_play(
    ws_communicator, channel_layer, party_factory, alice, bob
):
    party = await sync_to_async(party_factory)(joined_users=[alice])
    communicator, _, _ = await connect(ws_communicator, bob, party.id)
    await channel_layer.receive(consumers.STATE_MACHINE_CHANNEL_NAME)
    party.started_at = timezone.now()
    await party.asave()
    await models.PartyRound.objects.acreate(party=party, letter="A")

    await communicator.send_to(
        text_data=answers_message(name="Ana", submit_stop="on")
    )

    await assert_still_open(communicator)
    assert not await models.UserRoundAnswer.objects.filter(user=bob).aexists()
    assert consumers.STATE_MACHINE_CHANNEL_NAME not in channel_layer.channels


async def test_answer_longer_than_model_field_is_rejected(
    ws_communicator, started_party, alice
):
    communicator, _, _ = await connect(ws_communicator, alice, started_party.id)

    await communicator.send_to(text_data=answers_message(name="A" * 51, city="Arica"))

    assert "word-error" in await communicator.receive_from()
    await assert_still_open(communicator)
    answers = models.UserRoundAnswer.objects.filter(user=alice).exclude(value="")
    assert {a.field: a.value async for a in answers} == {"city": "Arica"}
