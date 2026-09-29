import msgpack
from channels.layers import InMemoryChannelLayer


class MsgpackInMemoryChannelLayer(InMemoryChannelLayer):
    """Round-trips every message through msgpack, as channels_redis does."""

    @staticmethod
    def _roundtrip(message):
        return msgpack.unpackb(msgpack.packb(message, use_bin_type=True), raw=False)

    async def send(self, channel, message):
        await super().send(channel, self._roundtrip(message))

    async def group_send(self, group, message):
        await super().group_send(group, self._roundtrip(message))
