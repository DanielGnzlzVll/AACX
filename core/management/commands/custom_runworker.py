import asyncio
import logging
import random

from channels.management.commands.runworker import Command as RunworkerCommand
from channels.worker import Worker

from core.consumers import abandon_idle_waiting_rooms, resume_orphaned_parties
from core.routing import channel_routing

logger = logging.getLogger(__name__)

RECONCILE_INTERVAL = 5


async def reconcile_parties(channel_layer):
    # Runs on every worker, so resuming parties doesn't depend on any single one;
    # the random offset spreads the passes of workers that start together.
    # Periodic rather than once at startup: a dead worker's lease only expires
    # after PartyLease.TTL, possibly well after this worker restarted.
    await asyncio.sleep(random.uniform(0, RECONCILE_INTERVAL))
    while True:
        try:
            await resume_orphaned_parties(channel_layer)
        except Exception:
            logger.exception("failed to resume orphaned parties")
        try:
            await abandon_idle_waiting_rooms()
        except Exception:
            logger.exception("failed to abandon idle waiting rooms")
        await asyncio.sleep(RECONCILE_INTERVAL)


class PartyWorker(Worker):
    async def handle(self):
        reconciler = asyncio.create_task(reconcile_parties(self.channel_layer))
        try:
            await super().handle()
        finally:
            reconciler.cancel()


class Command(RunworkerCommand):
    worker_class = PartyWorker

    def handle(self, *args, **options):
        if "*" in options["channels"]:
            options["channels"] = list(channel_routing.keys())
        super().handle(*args, **options)
