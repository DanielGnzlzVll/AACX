import asyncio

import pytest
from asgiref.sync import sync_to_async
from django.utils import timezone

from core import consumers, models

pytestmark = pytest.mark.django_db(transaction=True)

PATH = "/party/{}/"


@pytest.fixture
def carol(user_factory):
    return user_factory("carol")


@pytest.fixture
async def waiting_room(channel_layer, monkeypatch):
    monkeypatch.setattr(consumers.PartyStateMachine, "WAITING_POLL_INTERVAL", 0.1)
    machine = consumers.PartyStateMachine()
    machine.channel_layer = channel_layer
    tasks = []

    def wait(party):
        task = asyncio.create_task(machine.wait_players_to_join(party.id))
        tasks.append(task)
        return task

    yield wait

    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


@pytest.fixture
def connect_players(ws_connect, channel_layer):
    async def connect(party, *users):
        communicators = []
        for user in users:
            communicators.append(await ws_connect(user, PATH.format(party.id)))
            await channel_layer.receive(consumers.STATE_MACHINE_CHANNEL_NAME)
        return communicators

    return connect


async def receive_until(communicator, text, timeout=5):
    async with asyncio.timeout(timeout):
        while text not in await communicator.receive_from(timeout=timeout):
            pass


async def assert_still_waiting(party, task):
    await asyncio.sleep(0.5)
    assert not task.done()
    await party.arefresh_from_db()
    assert party.started_at is None


async def joined_usernames(party):
    return {user.username async for user in party.joined_users.all()}


async def test_one_user_with_several_connections_counts_once(
    waiting_room, connect_players, party_factory, alice, bob
):
    party = await sync_to_async(party_factory)(min_players=2)
    await connect_players(party, alice, alice)

    task = waiting_room(party)

    await assert_still_waiting(party, task)
    await connect_players(party, bob)
    started = await asyncio.wait_for(task, timeout=5)
    assert started.started_at is not None
    assert await joined_usernames(party) == {"alice", "bob"}


async def test_party_does_not_start_below_min_players(
    waiting_room, connect_players, party_factory, alice, bob, carol
):
    party = await sync_to_async(party_factory)(min_players=3)
    await connect_players(party, alice, bob)

    task = waiting_room(party)

    await assert_still_waiting(party, task)
    await connect_players(party, carol)
    assert await asyncio.wait_for(task, timeout=5)


async def test_every_participant_is_recorded(
    waiting_room, connect_players, party_factory, alice, bob, carol
):
    party = await sync_to_async(party_factory)(min_players=2)
    await connect_players(party, alice, bob, carol)

    assert await asyncio.wait_for(waiting_room(party), timeout=5)

    assert await joined_usernames(party) == {"alice", "bob", "carol"}


async def test_player_count_is_accurate_after_disconnects(
    waiting_room, connect_players, party_factory, alice, bob, carol
):
    party = await sync_to_async(party_factory)(min_players=3)
    alice_ws, _, bob_ws = await connect_players(party, alice, alice, bob)
    waiting_room(party)
    await receive_until(alice_ws, "Actualmente hay 2 jugadores")

    await bob_ws.disconnect()

    await receive_until(alice_ws, "Actualmente hay 1 jugadores")


async def test_stale_connections_are_not_counted(
    waiting_room, connect_players, party_factory, alice, bob
):
    party = await sync_to_async(party_factory)(min_players=3)
    alice_ws, _ = await connect_players(party, alice, bob)
    waiting_room(party)
    await receive_until(alice_ws, "Actualmente hay 2 jugadores")

    await models.PartyConnection.objects.filter(user=bob).aupdate(
        last_seen_at=timezone.now() - 2 * models.PartyConnection.TTL
    )

    await receive_until(alice_ws, "Actualmente hay 1 jugadores")


async def test_empty_waiting_room_releases_the_party(
    waiting_room, connect_players, party_factory, alice, bob
):
    party = await sync_to_async(party_factory)(min_players=2)
    (alice_ws,) = await connect_players(party, alice)
    task = waiting_room(party)
    await receive_until(alice_ws, "Actualmente hay 1 jugadores")

    await alice_ws.disconnect()

    assert await asyncio.wait_for(task, timeout=5) is None
    await party.arefresh_from_db()
    assert (party.waiting_started_at, party.started_at) == (None, None)
    await connect_players(party, alice, bob)
    assert await asyncio.wait_for(waiting_room(party), timeout=5)
