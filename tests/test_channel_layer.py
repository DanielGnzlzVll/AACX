import pytest
from django.contrib.auth.models import User


async def test_send_rejects_messages_redis_cannot_serialize(channel_layer):
    with pytest.raises(TypeError):
        await channel_layer.send("probe", {"type": "event", "user": User()})


async def test_group_send_rejects_messages_redis_cannot_serialize(channel_layer):
    await channel_layer.group_add("group", "probe")

    with pytest.raises(TypeError):
        await channel_layer.group_send("group", {"type": "event", "user": User()})
