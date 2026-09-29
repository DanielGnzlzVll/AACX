import asyncio
import json
import re
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
    # display_all_answers paces the reveal with sleeps; the presence heartbeat
    # stays real.
    real_sleep = asyncio.sleep
    heartbeat = models.PartyConnection.HEARTBEAT_INTERVAL.total_seconds()

    async def sleep(delay, *args, **kwargs):
        if delay < heartbeat:
            delay = 0
        return await real_sleep(delay, *args, **kwargs)

    fast_asyncio = types.ModuleType("asyncio")
    fast_asyncio.__dict__.update(vars(asyncio))
    fast_asyncio.sleep = sleep
    monkeypatch.setattr(consumers, "asyncio", fast_asyncio)


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


def is_answers_status(message):
    return 'id="answer_error_name"' in message and ANSWERS_FORM not in message


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
        await receive_until(ws, is_answers_status)

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


def is_party_content(message):
    return 'id="party_content"' in message


@pytest.mark.django_db(transaction=True)
async def test_past_answers_survive_the_next_round(
    ws_connect,
    channel_layer,
    state_machine,
    fast_answers_reveal,
    party_factory,
    alice,
    bob,
):
    party = await sync_to_async(party_factory)(
        min_players=2, max_rounds=2, max_round_duration=1
    )
    path = f"/party/{party.id}/"
    alice_ws = await ws_connect(alice, path)
    bob_ws = await ws_connect(bob, path)
    start_event = await channel_layer.receive(consumers.STATE_MACHINE_CHANNEL_NAME)
    party_task = asyncio.create_task(state_machine.play_party(start_event["party_id"]))

    for ws in (alice_ws, bob_ws):
        await receive_until(ws, is_answers_form)
    letter = (await models.PartyRound.objects.aget(party=party)).letter
    answers = {alice_ws: f"{letter}alicia", bob_ws: f"{letter}roberto"}
    for ws, name in answers.items():
        await ws.send_to(text_data=answers_message(name=name))
        await receive_until(ws, is_answers_status)

    for ws in (alice_ws, bob_ws):
        await receive_until(ws, is_disabled_answers_form)
    alice_round_2 = await receive_until(alice_ws, is_party_content)
    bob_round_2 = await receive_until(bob_ws, is_party_content)
    await asyncio.wait_for(party_task, timeout=10)

    assert answers[alice_ws] in alice_round_2
    assert answers[bob_ws] not in alice_round_2
    assert answers[bob_ws] in bob_round_2
    assert answers[alice_ws] not in bob_round_2


@pytest.mark.django_db(transaction=True)
async def test_group_broadcasts_carry_no_per_player_answers(
    monkeypatch,
    ws_connect,
    channel_layer,
    state_machine,
    fast_answers_reveal,
    party_factory,
    alice,
    bob,
):
    party = await sync_to_async(party_factory)(
        min_players=2, max_rounds=2, max_round_duration=1
    )
    broadcast_html = []
    group_send = channel_layer.group_send

    async def spy(group, message):
        if message["type"] == "html":
            broadcast_html.append(message["message"])
        await group_send(group, message)

    monkeypatch.setattr(channel_layer, "group_send", spy)
    path = f"/party/{party.id}/"
    await ws_connect(alice, path)
    await ws_connect(bob, path)
    start_event = await channel_layer.receive(consumers.STATE_MACHINE_CHANNEL_NAME)
    await asyncio.wait_for(state_machine.play_party(start_event["party_id"]), 15)

    assert broadcast_html
    for message in broadcast_html:
        assert "party_answers_table" not in ElementIds(message).all, message


@pytest.mark.django_db(transaction=True)
async def test_answers_reveal_titles_each_category_in_spanish(
    monkeypatch, channel_layer, state_machine, fast_answers_reveal, party_factory
):
    party = await sync_to_async(party_factory)()
    round = await models.PartyRound.objects.acreate(party=party, letter="M")
    titles = []
    group_send = channel_layer.group_send

    async def spy(group, message):
        titles.extend(re.findall(r"<h3>(.+?)</h3>", message["message"]))
        await group_send(group, message)

    monkeypatch.setattr(channel_layer, "group_send", spy)
    await state_machine.display_all_answers([], round, party)

    assert titles == ["Nombre", "Apellido", "País", "Ciudad", "Animal", "Cosa", "Color"]
