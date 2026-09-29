import pytest
from asgiref.sync import sync_to_async
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.models import PartyAccess

pytestmark = pytest.mark.django_db(transaction=True)

STATES = {
    "not-started": {},
    "started": {"started_at": timezone.now()},
    "closed": {"started_at": timezone.now(), "closed_at": timezone.now()},
}

CASES = [
    ("not-started", False, PartyAccess.WAITING),
    ("not-started", True, PartyAccess.PARTICIPANT),
    ("started", False, PartyAccess.STARTED),
    ("started", True, PartyAccess.PARTICIPANT),
    ("closed", False, PartyAccess.CLOSED),
    ("closed", True, PartyAccess.PARTICIPANT),
]


@pytest.mark.parametrize("state, joined, expected", CASES)
async def test_party_access(party_factory, alice, state, joined, expected):
    party = await sync_to_async(party_factory)(
        joined_users=[alice] if joined else [], **STATES[state]
    )

    assert await party.aget_access(alice) is expected


async def test_closed_party_that_never_started_is_closed_to_non_participants(
    party_factory, alice
):
    party = await sync_to_async(party_factory)(closed_at=timezone.now())

    assert await party.aget_access(alice) is PartyAccess.CLOSED


@pytest.mark.parametrize("state, joined, access", CASES)
async def test_page_connects_only_when_the_websocket_accepts(
    ws_communicator, party_factory, alice, state, joined, access
):
    party = await sync_to_async(party_factory)(
        joined_users=[alice] if joined else [], **STATES[state]
    )
    client = Client()
    await sync_to_async(client.force_login)(alice)

    response = await sync_to_async(client.get)(
        reverse("detail_party", kwargs={"party_id": party.id})
    )
    communicator = await ws_communicator(alice, f"/party/{party.id}/")
    connected, code = await communicator.connect()

    assert connected is access.can_play
    if response.status_code == 404:
        assert access is PartyAccess.CLOSED
    else:
        page_connects = "ws-connect" in response.content.decode()
        assert page_connects is connected
    if not connected:
        assert code == 4403
