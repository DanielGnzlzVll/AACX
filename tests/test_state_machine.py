import asyncio
import datetime
import logging
import types

import pytest
import redis
import redis_lock
from asgiref.sync import sync_to_async
from asgiref.testing import ApplicationCommunicator
from channels.routing import ChannelNameRouter
from django.apps import apps
from django.db import connection, connections
from django.utils import timezone

from core import consumers, models, routing
from core.leases import LeaseLost, PartyLease, get_redis_client
from core.management.commands import custom_runworker

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def clear_leases():
    def clear():
        client = get_redis_client()
        for key in client.scan_iter("lock:party-lease:*"):
            client.delete(key)

    clear()
    yield
    clear()


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
        party = await sync_to_async(party_factory)(min_players=2, max_round_duration=60)
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
    return await models.PartyRound.objects.filter(party=party, closed_at=None).afirst()


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


@pytest.fixture
def started_party(party_factory, alice):
    async def create(**fields):
        fields.setdefault("started_at", timezone.now())
        return await sync_to_async(party_factory)(joined_users=[alice], **fields)

    return create


async def open_round_started_ago(party, seconds, letter="A"):
    round = await models.PartyRound.objects.acreate(party=party, letter=letter)
    shift = datetime.timedelta(seconds=seconds)
    round.started_at -= shift
    round.deadline_at -= shift
    await round.asave(update_fields=["started_at", "deadline_at"])
    return round


def test_app_startup_runs_no_queries(django_assert_num_queries):
    with django_assert_num_queries(0):
        apps.get_app_config("core").ready()


async def test_resumed_round_only_waits_for_the_time_left(
    state_machine, started_party, instant_reveal
):
    party = await started_party(max_rounds=1, max_round_duration=60)
    round = await open_round_started_ago(party, 59.5)

    await asyncio.wait_for(state_machine.play_party(party.id), timeout=5)

    assert await is_closed(round)
    assert await models.PartyRound.objects.filter(party=party).acount() == 1
    await party.arefresh_from_db()
    assert party.closed_at is not None


async def test_resume_scores_an_unscored_last_round_without_opening_another(
    state_machine, started_party, instant_reveal, alice
):
    party = await started_party(max_rounds=2, max_round_duration=60)
    first = await open_round_started_ago(party, 120)
    await first.close_round_and_calculate_scores()
    last = await open_round_started_ago(party, 10, letter="B")
    await models.UserRoundAnswer.objects.acreate(
        round=last, user=alice, field="name", value="Bea"
    )
    await last.close(models.PartyRound.ClosedReason.STOP)

    await asyncio.wait_for(state_machine.play_party(party.id), timeout=5)

    assert await models.PartyRound.objects.filter(party=party).acount() == 2
    answer = await models.UserRoundAnswer.objects.aget(round=last)
    assert answer.scored_points == 100
    await party.arefresh_from_db()
    assert party.closed_at is not None


async def test_a_party_owned_by_another_worker_is_not_run_again(worker, started_party):
    party = await started_party()
    other_worker_lease = PartyLease(party.id)
    assert await other_worker_lease.acquire()

    try:
        await start(worker, party)
        await asyncio.sleep(0.3)
        assert not worker.consumer.party_tasks
        assert not await models.PartyRound.objects.filter(party=party).aexists()
    finally:
        await other_worker_lease.release()


async def test_only_orphaned_parties_are_resumed(channel_layer, started_party):
    orphaned = await started_party()
    owned = await started_party()
    await started_party(closed_at=timezone.now())
    await started_party(started_at=None)
    lease = PartyLease(owned.id)
    assert await lease.acquire()

    try:
        await consumers.resume_orphaned_parties(channel_layer)
    finally:
        await lease.release()

    message = await channel_layer.receive(consumers.STATE_MACHINE_CHANNEL_NAME)
    assert message["type"] == "event_party_started"
    assert message["party_id"] == orphaned.id
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.2):
            await channel_layer.receive(consumers.STATE_MACHINE_CHANNEL_NAME)


async def test_a_party_is_resumed_once_its_dead_owner_lease_expires(
    channel_layer, started_party
):
    party = await started_party()
    dead_owner_lease = redis_lock.Lock(
        get_redis_client(), f"party-lease:{party.id}", expire=1
    )
    assert dead_owner_lease.acquire(blocking=False)

    await consumers.resume_orphaned_parties(channel_layer)
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.2):
            await channel_layer.receive(consumers.STATE_MACHINE_CHANNEL_NAME)

    await asyncio.sleep(1.2)
    await consumers.resume_orphaned_parties(channel_layer)
    message = await channel_layer.receive(consumers.STATE_MACHINE_CHANNEL_NAME)
    assert message["party_id"] == party.id


@pytest.fixture
async def start_party_worker(channel_layer, monkeypatch):
    monkeypatch.setattr(custom_runworker, "RECONCILE_INTERVAL", 0.05)
    handles = []

    def start():
        worker = custom_runworker.PartyWorker(
            application=ChannelNameRouter(routing.channel_routing),
            channels=[consumers.STATE_MACHINE_CHANNEL_NAME],
            channel_layer=channel_layer,
        )
        handles.append(asyncio.create_task(worker.handle()))
        return worker

    yield start

    tasks = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
    for task in tasks:
        task.cancel()
    await asyncio.gather(*handles, *tasks, return_exceptions=True)
    await sync_to_async(connections.close_all)()


async def test_a_restarted_worker_continues_an_interrupted_party_at_its_round(
    started_party, start_party_worker
):
    party = await started_party(max_rounds=3, max_round_duration=60)
    round = await open_round_started_ago(party, 10)
    start_party_worker()

    await eventually(lambda: PartyLease.is_held(party.id))

    await asyncio.sleep(0.2)
    assert await open_round(party) == round
    assert await models.PartyRound.objects.filter(party=party).acount() == 1


async def test_any_surviving_worker_resumes_a_party_whose_owner_died(
    started_party, start_party_worker
):
    party = await started_party(max_rounds=3, max_round_duration=60)
    round = await open_round_started_ago(party, 10)
    dead_owner_lease = redis_lock.Lock(
        get_redis_client(), f"party-lease:{party.id}", expire=1
    )
    assert dead_owner_lease.acquire(blocking=False)
    start_party_worker()
    start_party_worker()

    async def resumed_by_a_live_worker():
        owner = await asyncio.to_thread(dead_owner_lease.get_owner_id)
        return owner is not None and owner != dead_owner_lease.id

    await eventually(resumed_by_a_live_worker, timeout=2)

    assert await open_round(party) == round
    assert await models.PartyRound.objects.filter(party=party).acount() == 1


async def test_a_runner_that_loses_its_lease_stops_before_anyone_takes_over(
    start_worker, started_party, instant_reveal, monkeypatch
):
    monkeypatch.setattr(PartyLease, "RENEW_INTERVAL", 0.05)
    first, second = await start_worker(), await start_worker()
    party = await started_party(max_rounds=3, max_round_duration=60)
    await start(first, party)
    await eventually(lambda: open_round(party))

    get_redis_client().delete(f"lock:party-lease:{party.id}")

    async def first_stopped():
        return not first.consumer.party_tasks

    await eventually(first_stopped, timeout=2)
    await models.Party.objects.filter(id=party.id).aupdate(max_round_duration=0)
    await models.PartyRound.objects.filter(party=party).aupdate(
        deadline_at=timezone.now()
    )
    await start(second, party)

    async def party_closed():
        await party.arefresh_from_db()
        return party.closed_at is not None

    await eventually(party_closed)
    assert await models.PartyRound.objects.filter(party=party).acount() == 3


async def test_the_lease_is_given_up_before_it_expires_when_renewals_fail(
    started_party, monkeypatch
):
    monkeypatch.setattr(PartyLease, "TTL", 2)
    monkeypatch.setattr(PartyLease, "RENEW_INTERVAL", 0.2)
    party = await started_party()
    lease = PartyLease(party.id)
    assert await lease.acquire()

    def redis_down(*args, **kwargs):
        raise redis.ConnectionError("down")

    monkeypatch.setattr(lease._lock, "extend", redis_down)
    loop = asyncio.get_running_loop()
    started_at = loop.time()

    with pytest.raises(LeaseLost):
        await lease.run_while_held(asyncio.sleep(10))

    assert 1.5 <= loop.time() - started_at < 2


async def test_concurrent_runners_open_a_single_round(started_party):
    party = await started_party()

    def next_round():
        try:
            return party._get_current_or_next_round()
        finally:
            connection.close()

    rounds = await asyncio.gather(
        asyncio.to_thread(next_round), asyncio.to_thread(next_round)
    )

    assert rounds[0] == rounds[1]
    assert await models.PartyRound.objects.filter(party=party).acount() == 1


async def test_a_closed_party_gets_no_new_round(started_party):
    party = await started_party(closed_at=timezone.now())
    last = await open_round_started_ago(party, 10)
    await last.close(models.PartyRound.ClosedReason.STOP)

    assert await party.aget_current_or_next_round() == last
    assert await models.PartyRound.objects.filter(party=party).acount() == 1
