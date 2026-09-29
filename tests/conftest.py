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

from core import models, routing


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
async def ws_connect(channel_layer):
    application = AuthMiddlewareStack(URLRouter(routing.websocket_urlpatterns))
    communicators = []

    async def connect(user, path):
        client = Client()
        await sync_to_async(client.force_login)(user)
        session_id = client.cookies[settings.SESSION_COOKIE_NAME].value
        communicator = WebsocketCommunicator(
            application,
            path,
            headers=[(b"cookie", f"{settings.SESSION_COOKIE_NAME}={session_id}".encode())],
        )
        connected, _ = await communicator.connect()
        assert connected
        communicators.append(communicator)
        return communicator

    yield connect

    for communicator in communicators:
        await communicator.disconnect()
    await sync_to_async(connections.close_all)()
