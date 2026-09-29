import asyncio
import functools

import redis
import redis_lock
from django.conf import settings


@functools.cache
def get_redis_client():
    return redis.Redis.from_url(settings.LEASE_REDIS_URL)


class PartyLease:
    """Makes one worker the owner of a party while it runs it.

    A thread renews the lease while it is held, so when its worker dies it
    expires within TTL seconds and another worker can resume the party.
    """

    TTL = 15

    def __init__(self, party_id):
        self._lock = self._make_lock(party_id, expire=self.TTL, auto_renewal=True)

    @staticmethod
    def _make_lock(party_id, **kwargs):
        return redis_lock.Lock(get_redis_client(), f"party-lease:{party_id}", **kwargs)

    @classmethod
    async def is_held(cls, party_id):
        return await asyncio.to_thread(cls._make_lock(party_id).locked)

    async def acquire(self):
        return await asyncio.to_thread(self._lock.acquire, blocking=False)

    async def release(self):
        try:
            await asyncio.to_thread(self._lock.release)
        except redis_lock.NotAcquired:
            pass
