import asyncio

import pytest
from channels_redis.core import RedisChannelLayer
from django.conf import settings
from django.contrib.auth.models import User

from asacx import settings as base_settings


async def test_send_rejects_messages_redis_cannot_serialize(channel_layer):
    with pytest.raises(TypeError):
        await channel_layer.send("probe", {"type": "event", "user": User()})


async def test_group_send_rejects_messages_redis_cannot_serialize(channel_layer):
    await channel_layer.group_add("group", "probe")

    with pytest.raises(TypeError):
        await channel_layer.group_send("group", {"type": "event", "user": User()})


async def test_redis_channel_layer_survives_an_idle_receive():
    config = base_settings.CHANNEL_LAYERS["default"]["CONFIG"]
    address = settings.LEASE_REDIS_URL.rsplit("/", 1)[0] + "/2"
    hosts = [{**host, "address": address} for host in config["hosts"]]
    layer = RedisChannelLayer(**{**config, "hosts": hosts})
    channel = await layer.new_channel()
    receiving = asyncio.ensure_future(layer.receive(channel))
    try:
        await asyncio.sleep(layer.brpop_timeout + 1)
        await layer.send(channel, {"type": "ping"})

        assert await asyncio.wait_for(receiving, timeout=2) == {"type": "ping"}
    finally:
        receiving.cancel()
        await layer.flush()
