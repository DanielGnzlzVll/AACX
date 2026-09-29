import asyncio
import functools
import logging

import redis
import redis_lock
from django.conf import settings

logger = logging.getLogger(__name__)


@functools.cache
def get_redis_client():
    return redis.Redis.from_url(
        settings.LEASE_REDIS_URL, socket_timeout=2, socket_connect_timeout=2
    )


class LeaseLost(Exception):
    pass


class PartyLease:
    """Makes one worker the owner of a party while it runs it.

    When its worker dies the lease expires within TTL seconds, and another
    worker can resume the party.
    """

    TTL = 15
    RENEW_INTERVAL = TTL / 3

    def __init__(self, party_id):
        self.party_id = party_id
        self._lock = self._make_lock(party_id, expire=self.TTL)

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

    async def run_while_held(self, coro):
        """Runs coro, and cancels it as soon as the lease may be lost.

        Raises LeaseLost in that case, before the lease can expire and another
        worker can take the party over.
        """
        work = asyncio.ensure_future(coro)
        keep_alive = asyncio.create_task(self._keep_alive())
        try:
            await asyncio.wait({work, keep_alive}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            keep_alive.cancel()
            if not work.done():
                work.cancel()
            await asyncio.gather(work, keep_alive, return_exceptions=True)
        if work.cancelled():
            raise LeaseLost(f"lost the lease of party {self.party_id}")
        return work.result()

    async def _keep_alive(self):
        loop = asyncio.get_running_loop()
        renewed_at = loop.time()
        while True:
            await asyncio.sleep(self.RENEW_INTERVAL)
            attempted_at = loop.time()
            try:
                await asyncio.to_thread(self._lock.extend)
            except redis_lock.NotAcquired:
                return
            except redis.RedisError:
                logger.warning(
                    f"failed to renew the lease of party {self.party_id}",
                    exc_info=True,
                )
                if loop.time() - renewed_at >= self.TTL - self.RENEW_INTERVAL:
                    return
            else:
                renewed_at = attempted_at
