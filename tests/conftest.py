import asyncio
import itertools

import pytest
from asgiref.sync import sync_to_async
from channels.auth import AuthMiddlewareStack
from channels.layers import get_channel_layer
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.contrib.auth.models import User
from django.db import connections
from django.test import Client

from core import consumers, models, routing


@pytest.fixture
def user_factory(db):
    def create_user(username):
        return User.objects.create_user(username=username)

    return create_user


@pytest.fixture
def alice(user_factory):
    return user_factory("alice")


@pytest.fixture
def bob(user_factory):
    return user_factory("bob")


@pytest.fixture
def party_factory(db):
    names = (f"party {i}" for i in itertools.count(1))

    def create_party(*, joined_users=(), **fields):
        fields.setdefault("name", next(names))
        party = models.Party.objects.create(**fields)
        party.joined_users.add(*joined_users)
        return party

    return create_party


@pytest.fixture
def logged_in_client(alice):
    client = Client()
    client.force_login(alice)
    return client


@pytest.fixture
async def channel_layer():
    layer = get_channel_layer()
    yield layer
    await layer.flush()


@pytest.fixture
def receive_or_none(channel_layer):
    async def receive(channel, timeout=0.2):
        try:
            return await asyncio.wait_for(channel_layer.receive(channel), timeout)
        except TimeoutError:
            return None

    return receive


@pytest.fixture
def state_machine(channel_layer):
    machine = consumers.PartyStateMachine()
    machine.channel_layer = channel_layer
    return machine


@pytest.fixture
def instant_reveal(monkeypatch):
    async def noop(*args, **kwargs):
        pass

    monkeypatch.setattr(consumers.PartyStateMachine, "display_all_answers", noop)


@pytest.fixture
async def ws_communicator(channel_layer):
    application = AuthMiddlewareStack(URLRouter(routing.websocket_urlpatterns))
    communicators = []

    async def create(user, path):
        headers = []
        if user is not None:
            client = Client()
            await sync_to_async(client.force_login)(user)
            session_id = client.cookies[settings.SESSION_COOKIE_NAME].value
            headers.append(
                (b"cookie", f"{settings.SESSION_COOKIE_NAME}={session_id}".encode())
            )
        communicator = WebsocketCommunicator(application, path, headers=headers)
        communicators.append(communicator)
        return communicator

    yield create

    for communicator in communicators:
        await communicator.disconnect()
    await sync_to_async(connections.close_all)()


@pytest.fixture
async def ws_connect(ws_communicator):
    async def connect(user, path):
        communicator = await ws_communicator(user, path)
        connected, _ = await communicator.connect()
        assert connected
        return communicator

    return connect
