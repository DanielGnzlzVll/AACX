import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.apps import AppConfig
from django.conf import settings
from django.db import transaction

logger = logging.getLogger(__name__)


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self):

        if settings.IS_CHANNELS_WORKER_MASTER:
            from core import models
            from core.consumers import STATE_MACHINE_CHANNEL_NAME

            channel_layer = get_channel_layer()
            with transaction.atomic():
                for party in models.Party.objects.select_for_update(
                    skip_locked=True
                ).filter(started_at__isnull=False, closed_at__isnull=True):
                    logger.info(f"trying to start {party.id=} after restart or crash")
                    async_to_sync(channel_layer.send)(
                        STATE_MACHINE_CHANNEL_NAME,
                        {
                            "type": "event_party_started",
                            "party_name": party.name,
                            "party_id": party.id,
                            "force_start": True,
                        },
                    )
