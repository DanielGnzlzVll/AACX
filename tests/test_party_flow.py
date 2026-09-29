import asyncio
import json
import types
from html.parser import HTMLParser

import pytest
from asgiref.sync import sync_to_async
from django.test import Client
from django.urls import reverse

from core import consumers, models

ANSWERS_FORM = 'id="party_current_answers_form"'


@pytest.fixture
def fast_answers_reveal(monkeypatch):
    # display_all_answers paces the reveal with sleeps; the join timeout stays real.
    real_sleep = asyncio.sleep

    async def sleep(delay, *args, **kwargs):
        if delay < consumers.PartyStateMachine.MAX_WAITING_TIME:
            delay = 0
        return await real_sleep(delay, *args, **kwargs)

    fast_asyncio = types.ModuleType("asyncio")
    fast_asyncio.__dict__.update(vars(asyncio))
    fast_asyncio.sleep = sleep
    monkeypatch.setattr(consumers, "asyncio", fast_asyncio)


@pytest.fixture
async def state_machine(channel_layer, monkeypatch):
    # The real implementation reads channels_redis internals.
    async def get_connected_players(self, group):
        return list(channel_layer.groups.get(group, {}))

    monkeypatch.setattr(
        consumers.PartyStateMachine, "get_connected_players", get_connected_players
    )
    machine = consumers.PartyStateMachine()
    machine.channel_layer = channel_layer
    yield machine

    # Cancel the join timeout left pending by the state machine.
    for task in asyncio.all_tasks():
        if task.get_name() == "timeout":
            task.cancel()


def answers_message(**answers):
    return json.dumps(
        {"HEADERS": {"HX-Trigger": "party_current_answers_form"}, **answers}
    )


async def receive_until(communicator, predicate, timeout=5):
    while True:
        message = await communicator.receive_from(timeout=timeout)
        if predicate(message):
            return message


def is_answers_form(message):
    return ANSWERS_FORM in message and "ws-send" in message


def is_disabled_answers_form(message):
    return ANSWERS_FORM in message and "ws-send" not in message


class ElementIds(HTMLParser):
    VOID_ELEMENTS = {"br", "hr", "img", "input", "link", "meta"}

    def __init__(self, html):
        super().__init__()
        self.depth = 0
        self.all = set()
        self.top_level = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        id = dict(attrs).get("id")
        if id:
            self.all.add(id)
        if self.depth == 0:
            self.top_level.append(id)
        if tag not in self.VOID_ELEMENTS:
            self.depth += 1

    def handle_endtag(self, tag):
        if tag not in self.VOID_ELEMENTS:
            self.depth -= 1


@pytest.mark.django_db(transaction=True)
async def test_two_players_play_a_round(
    ws_connect,
    channel_layer,
    state_machine,
    fast_answers_reveal,
    party_factory,
    alice,
    bob,
):
    party = await sync_to_async(party_factory)(
        min_players=2, max_rounds=1, max_round_duration=2
    )
    path = f"/party/{party.id}/"

    alice_ws = await ws_connect(alice, path)
    bob_ws = await ws_connect(bob, path)
    start_event = await channel_layer.receive(consumers.STATE_MACHINE_CHANNEL_NAME)
    party_task = asyncio.create_task(state_machine.play_party(start_event["party_id"]))

    for ws in (alice_ws, bob_ws):
        await receive_until(ws, is_answers_form)
    round = await models.PartyRound.objects.aget(party=party)
    letter = round.letter

    await alice_ws.send_to(
        text_data=answers_message(name=f"{letter}lice", country=f"{letter}land")
    )
    await bob_ws.send_to(
        text_data=answers_message(
            name=f"{letter}ob", country=f"{letter}land", animal="1gato"
        )
    )
    for ws in (alice_ws, bob_ws):
        await receive_until(ws, is_answers_form)

    for ws in (alice_ws, bob_ws):
        await receive_until(ws, is_disabled_answers_form)
    await asyncio.wait_for(party_task, timeout=10)

    await round.arefresh_from_db()
    await party.arefresh_from_db()
    assert round.closed_at is not None
    assert party.started_at is not None
    assert {user.username async for user in party.joined_users.all()} == {
        "alice",
        "bob",
    }
    scores = {
        (answer.user.username, answer.field): answer.scored_points or 0
        async for answer in models.UserRoundAnswer.objects.filter(
            round=round, field__in=["name", "country"]
        ).select_related("user")
    }
    assert scores == {
        ("alice", "name"): 100,
        ("alice", "country"): 50,
        ("bob", "name"): 100,
        ("bob", "country"): 50,
    }
    assert await party.aget_players_scores() == {"alice": 150, "bob": 150}


@pytest.mark.django_db(transaction=True)
async def test_waiting_page_receives_every_broadcast(
    ws_connect,
    channel_layer,
    state_machine,
    fast_answers_reveal,
    party_factory,
    alice,
    bob,
):
    party = await sync_to_async(party_factory)(
        min_players=2, max_rounds=1, max_round_duration=1
    )
    client = Client()
    await sync_to_async(client.force_login)(alice)
    response = await sync_to_async(client.get)(
        reverse("detail_party", kwargs={"party_id": party.id})
    )
    assert "party_no_started.html" in [t.name for t in response.templates]
    page_ids = ElementIds(response.content.decode()).all

    path = f"/party/{party.id}/"
    alice_ws = await ws_connect(alice, path)
    await ws_connect(bob, path)
    start_event = await channel_layer.receive(consumers.STATE_MACHINE_CHANNEL_NAME)
    party_task = asyncio.create_task(state_machine.play_party(start_event["party_id"]))

    # The htmx ws extension swaps each top-level element into the element with its id.
    while True:
        message = await alice_ws.receive_from(timeout=5)
        ids = ElementIds(message)
        assert set(ids.top_level) <= page_ids, message
        page_ids |= ids.all
        if "party_finished" in ids.all:
            break
    await asyncio.wait_for(party_task, timeout=10)
