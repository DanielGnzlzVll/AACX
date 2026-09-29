import asyncio
import logging
import types

import pytest
from asgiref.sync import sync_to_async
from asgiref.testing import ApplicationCommunicator
from django.utils import timezone

from core import consumers, models

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
async def start_worker(channel_layer):
    workers = []

    async def start():
        consumer = consumers.PartyStateMachine()
        communicator = ApplicationCommunicator(
            consumer,
            {"type": "channel", "channel": consumers.STATE_MACHINE_CHANNEL_NAME},
        )
        worker = types.SimpleNamespace(consumer=consumer, communicator=communicator)
        workers.append(worker)
        return worker

    yield start

    for worker in workers:
        worker.communicator.stop(exceptions=False)
        tasks = [worker.communicator.future, *worker.consumer.party_tasks.values()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.fixture
async def worker(start_worker):
    return await start_worker()


@pytest.fixture
def ready_party(party_factory, alice, bob):
    async def create():
        party = await sync_to_async(party_factory)(
            min_players=2, max_round_duration=60
        )
        for user in (alice, bob):
            await models.PartyConnection.objects.acreate(
                party=party,
                user=user,
                channel_name=f"{party.id}-{user.username}",
                last_seen_at=timezone.now(),
            )
        return party

    return create


async def start(worker, party):
    await worker.communicator.send_input(
        {"type": "event_party_started", "party_id": party.id}
    )


async def stop(worker, round):
    await worker.communicator.send_input(
        {
            "type": "event_party_round_stopped",
            "party_id": round.party_id,
            "round_id": round.id,
        }
    )


async def open_round(party):
    return await models.PartyRound.objects.filter(
        party=party, closed_at=None
    ).afirst()


async def is_closed(round):
    await round.arefresh_from_db()
    return round.closed_at is not None


async def eventually(predicate, timeout=5):
    async with asyncio.timeout(timeout):
        while not (result := await predicate()):
            await asyncio.sleep(0.05)
    return result


async def test_parties_on_one_worker_all_start_within_seconds(worker, ready_party):
    parties = [await ready_party() for _ in range(10)]

    for party in parties:
        await start(worker, party)

    async def all_playing():
        return all([await open_round(party) for party in parties])

    await eventually(all_playing)
    assert await models.Party.objects.filter(
        id__in=[party.id for party in parties], started_at__isnull=False
    ).acount() == len(parties)


async def test_stop_is_handled_while_another_party_round_runs(worker, ready_party):
    busy, stopped = await ready_party(), await ready_party()
    await start(worker, busy)
    busy_round = await eventually(lambda: open_round(busy))
    await start(worker, stopped)
    stopped_round = await eventually(lambda: open_round(stopped))

    await stop(worker, stopped_round)

    await eventually(lambda: is_closed(stopped_round), timeout=2)
    assert not await is_closed(busy_round)


async def test_a_crashing_party_does_not_affect_other_parties(
    worker, ready_party, monkeypatch, caplog
):
    broken, healthy = await ready_party(), await ready_party()
    next_round = consumers.PartyStateMachine.next_round

    async def next_round_failing_for_broken(self, party):
        if party.id == broken.id:
            raise RuntimeError("boom")
        return await next_round(self, party)

    monkeypatch.setattr(
        consumers.PartyStateMachine, "next_round", next_round_failing_for_broken
    )
    monkeypatch.setattr(logging.getLogger("core"), "propagate", True)

    async def crashed():
        return broken.id not in worker.consumer.party_tasks

    with caplog.at_level(logging.ERROR, logger=consumers.logger.name):
        await start(worker, broken)
        await eventually(crashed)
        await start(worker, healthy)
        healthy_round = await eventually(lambda: open_round(healthy))
        await stop(worker, healthy_round)
        await eventually(lambda: is_closed(healthy_round), timeout=2)

    assert f"party {broken.id} crashed" in caplog.text
    assert not worker.communicator.future.done()


async def test_a_party_runs_once_across_duplicate_starts_and_workers(
    start_worker, ready_party
):
    first, second = await start_worker(), await start_worker()
    party = await ready_party()

    for worker in (first, first, second):
        await start(worker, party)

    await eventually(lambda: open_round(party))
    await asyncio.sleep(0.2)
    assert len(first.consumer.party_tasks) + len(second.consumer.party_tasks) == 1
    assert await models.PartyRound.objects.filter(party=party).acount() == 1
