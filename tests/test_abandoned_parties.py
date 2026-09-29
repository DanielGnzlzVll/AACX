import asyncio
import datetime

import pytest
from asgiref.sync import sync_to_async
from django.urls import reverse
from django.utils import timezone

from core import consumers, forms, models
from core.management.commands import custom_runworker

pytestmark = pytest.mark.django_db(transaction=True)

IDLE_FOR = datetime.timedelta(minutes=30)


def ago(delta):
    return timezone.now() - delta


@pytest.fixture
def idle_party(party_factory):
    def create(created=2 * IDLE_FOR, **fields):
        party = party_factory(**fields)
        models.Party.objects.filter(pk=party.pk).update(created_at=ago(created))
        return party

    return sync_to_async(create)


async def connect(party, user, seen=datetime.timedelta(0), channel_name=None):
    await models.PartyConnection.objects.acreate(
        party=party,
        user=user,
        channel_name=channel_name or f"{user.username}-{party.pk}",
        last_seen_at=ago(seen),
    )


async def status(party):
    await party.arefresh_from_db()
    return party.status


async def test_idle_waiting_party_is_abandoned_and_closed(idle_party):
    party = await idle_party()

    assert await models.Party.objects.aabandon_idle(IDLE_FOR) == 1

    await party.arefresh_from_db()
    assert party.status == models.PartyStatus.ABANDONED
    assert party.closed_reason == models.PartyClosedReason.ABANDONED
    assert party.closed_at is not None
    assert party.started_at is None


async def test_new_waiting_party_is_not_abandoned(idle_party):
    party = await idle_party(created=IDLE_FOR / 2)

    assert await models.Party.objects.aabandon_idle(IDLE_FOR) == 0
    assert await status(party) == models.PartyStatus.WAITING


async def test_waiting_party_with_a_live_connection_is_never_abandoned(
    idle_party, alice
):
    party = await idle_party(last_seen_at=ago(2 * IDLE_FOR))
    await connect(party, alice)

    assert await models.Party.objects.aabandon_idle(IDLE_FOR) == 0
    assert await status(party) == models.PartyStatus.WAITING


async def test_party_a_player_left_recently_is_not_abandoned(idle_party):
    party = await idle_party(last_seen_at=ago(IDLE_FOR / 2))

    assert await models.Party.objects.aabandon_idle(IDLE_FOR) == 0
    assert await status(party) == models.PartyStatus.WAITING


async def test_party_whose_connections_died_recently_is_not_abandoned(
    idle_party, alice
):
    party = await idle_party(last_seen_at=ago(2 * IDLE_FOR))
    await connect(party, alice, seen=IDLE_FOR / 2)

    assert await models.Party.objects.aabandon_idle(IDLE_FOR) == 0

    await models.PartyConnection.objects.aupdate(last_seen_at=ago(2 * IDLE_FOR))
    assert await models.Party.objects.aabandon_idle(IDLE_FOR) == 1


@pytest.mark.parametrize(
    "fields, expected",
    [
        ({"started_at": timezone.now()}, models.PartyStatus.PLAYING),
        (
            {"started_at": timezone.now(), "closed_at": timezone.now()},
            models.PartyStatus.FINISHED,
        ),
    ],
)
async def test_started_parties_are_never_abandoned(idle_party, fields, expected):
    party = await idle_party(**fields)

    assert await models.Party.objects.aabandon_idle(IDLE_FOR) == 0
    assert await status(party) == expected


async def test_abandoned_party_leaves_the_lobby_and_frees_its_name(idle_party, alice):
    party = await idle_party(name="Sala", joined_users=[alice])

    await models.Party.objects.aabandon_idle(IDLE_FOR)

    available = models.Party.objects.get_available_parties(alice)
    assert not await available.filter(pk=party.pk).aexists()
    form = forms.PartyForm(
        {"name": "sala", "min_players": 2, "max_round_duration": 60, "max_rounds": 3}
    )
    assert await sync_to_async(form.is_valid)(), form.errors
    await sync_to_async(form.save)()


async def test_abandoned_party_is_closed_to_its_players(
    idle_party, alice, ws_communicator
):
    party = await idle_party(joined_users=[alice])
    await models.Party.objects.aabandon_idle(IDLE_FOR)
    await party.arefresh_from_db()

    assert await party.aget_access(alice) is models.PartyAccess.CLOSED
    communicator = await ws_communicator(alice, f"/party/{party.id}/")
    assert await communicator.connect() == (False, 4403)


async def test_player_connecting_while_the_party_is_abandoned_is_rejected(
    idle_party, alice, ws_communicator, monkeypatch
):
    party = await idle_party()
    can_join = consumers.PartyConsumer.can_join

    async def abandoned_after_the_check(self, user):
        allowed = await can_join(self, user)
        await models.Party.objects.aabandon_idle(IDLE_FOR)
        return allowed

    monkeypatch.setattr(consumers.PartyConsumer, "can_join", abandoned_after_the_check)
    communicator = await ws_communicator(alice, f"/party/{party.id}/")

    assert await communicator.connect() == (False, 4403)
    assert not await models.PartyConnection.objects.aexists()


async def test_abandoned_party_page_is_not_found(idle_party, alice, logged_in_client):
    party = await idle_party(joined_users=[alice])
    await models.Party.objects.aabandon_idle(IDLE_FOR)

    response = await sync_to_async(logged_in_client.get)(
        reverse("detail_party", kwargs={"party_id": party.id})
    )

    assert response.status_code == 404


async def test_abandoned_party_cannot_be_started(idle_party, state_machine):
    party = await idle_party()
    await models.Party.objects.aabandon_idle(IDLE_FOR)

    assert not await state_machine.start_party(party)
    assert await status(party) == models.PartyStatus.ABANDONED


async def test_joining_and_leaving_the_waiting_room_marks_the_party_as_seen(
    idle_party, alice, ws_connect, channel_layer
):
    party = await idle_party()
    communicator = await ws_connect(alice, f"/party/{party.id}/")
    await party.arefresh_from_db()
    joined_at = party.last_seen_at
    assert joined_at > ago(IDLE_FOR)

    await communicator.disconnect()

    await party.arefresh_from_db()
    assert party.last_seen_at > joined_at
    assert await models.Party.objects.aabandon_idle(IDLE_FOR) == 0


async def test_player_leaving_a_long_wait_never_looks_like_an_empty_room(
    idle_party, alice, ws_connect, monkeypatch
):
    party = await idle_party()
    communicator = await ws_connect(alice, f"/party/{party.id}/")
    await models.Party.objects.filter(pk=party.pk).aupdate(
        last_seen_at=ago(2 * IDLE_FOR)
    )
    adelete = models.PartyConnectionQuerySet.adelete
    swept = []

    async def sweep_after_delete(self):
        result = await adelete(self)
        swept.append(await models.Party.objects.aabandon_idle(IDLE_FOR))
        return result

    monkeypatch.setattr(models.PartyConnectionQuerySet, "adelete", sweep_after_delete)
    await communicator.disconnect()

    assert swept == [0]
    assert await status(party) == models.PartyStatus.WAITING


async def test_every_worker_abandons_idle_waiting_rooms(
    idle_party, channel_layer, settings, monkeypatch
):
    settings.PARTY_ABANDON_AFTER = IDLE_FOR.total_seconds()
    monkeypatch.setattr(custom_runworker, "RECONCILE_INTERVAL", 0.01)
    party = await idle_party()

    reconciler = asyncio.create_task(custom_runworker.reconcile_parties(channel_layer))
    try:
        async with asyncio.timeout(5):
            while await status(party) != models.PartyStatus.ABANDONED:
                await asyncio.sleep(0.01)
    finally:
        reconciler.cancel()
        await asyncio.gather(reconciler, return_exceptions=True)


async def test_abandon_idle_waiting_rooms_uses_the_configured_delay(
    idle_party, settings
):
    party = await idle_party(created=datetime.timedelta(minutes=10))

    settings.PARTY_ABANDON_AFTER = 15 * 60
    await consumers.abandon_idle_waiting_rooms()
    assert await status(party) == models.PartyStatus.WAITING

    settings.PARTY_ABANDON_AFTER = 5 * 60
    await consumers.abandon_idle_waiting_rooms()
    assert await status(party) == models.PartyStatus.ABANDONED
