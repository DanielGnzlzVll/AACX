import asyncio
import logging

from channels.management.commands.runworker import Command as RunworkerCommand
from channels.worker import Worker
from django.conf import settings

from core.consumers import resume_orphaned_parties
from core.routing import channel_routing

logger = logging.getLogger(__name__)

RECONCILE_INTERVAL = 5


async def reconcile_parties(channel_layer):
    # Periodic rather than once at startup: a dead worker's lease only expires
    # after PartyLease.TTL, possibly well after this worker restarted.
    while True:
        try:
            await resume_orphaned_parties(channel_layer)
        except Exception:
            logger.exception("failed to resume orphaned parties")
        await asyncio.sleep(RECONCILE_INTERVAL)


class PartyWorker(Worker):
    async def handle(self):
        if not settings.IS_CHANNELS_WORKER_MASTER:
            return await super().handle()
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
