import logging

from asgiref.sync import async_to_sync

import datetime

from channels.generic.websocket import (
    SyncConsumer,
    AsyncWebsocketConsumer,
)

from django.db import transaction

from django.template.loader import render_to_string

from core import models

logger = logging.getLogger(__name__)


class PartyConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.party_id = self.scope["url_route"]["kwargs"]["party_id"]
        user = self.scope["user"]
        await self.accept()
        logger.info(f"player connected to party: {self.party_id} {user.username=}")

        self.party_group_name = "party_%s" % self.party_id
        await self.channel_layer.group_add(self.party_group_name, self.channel_name)

        await self.channel_layer.send(
            f"party_players_{self.party_id}",
            {
                "hola": "mundo",
                "date": datetime.datetime.now().isoformat(),
                "party_id": self.party_id,
                "username": user.username,
                "user_id": user.id,
            },
        )

        party = await models.Party.objects.aget(id=self.party_id)

        if not party.started_at:
            logger.info(f"party no started yet {self.party_id=}")
            await self.channel_layer.send(
                "party-state-machine",
                {
                    "type": "party_started",
                    "party_name": party.name,
                    "party_id": party.id,
                },
            )
            await self.send(text_data="waiting for players to join")

    async def receive(self, text_data):
        logger.info(f"receive {text_data=}")

    async def html(self, event):
        logger.info(f"html event {event=}")
        await self.send(text_data=event["message"])

    async def disconnect(self, close_code):
        logger.info(
            f"player disconnected from party: {self.party_id} {self.scope['user'].username=}"
        )


class PartyStateMachine(SyncConsumer):
    def party_started(self, event):
        party_id = event["party_id"]
        logger.info(f"starting {party_id=}")
        with transaction.atomic():
            for party in models.Party.objects.select_for_update(
                skip_locked=True
            ).filter(id=party_id, started_at=None):
                logger.info("---- waiting players to join")
                for i in range(2):
                    logger.info("---- waiting new player to join")
                    player_data = async_to_sync(self.channel_layer.receive)(
                        f"party_players_{party_id}"
                    )
                    party.joined_users.add(player_data["user_id"])

                    current_players = async_to_sync(self.get_connected_players)(
                        f"party_{party_id}"
                    )
                    msg = f"""<div id="party_content">
                        Esperando Mas Jugadores...
                        Actualmente hay {len(current_players)} jugadores
                    </div>
                    """
                    async_to_sync(self.channel_layer.group_send)(
                        f"party_{party_id}", {"type": "html", "message": msg}
                    )

                    logger.info(f"player joined {player_data=}")
                logger.info("all players joined")
                party.started_at = datetime.datetime.now()
                party.save()

                template_string = render_to_string(
                    "party.html",
                    {
                        "party": party,
                        "current_round": party.get_current_or_next_round(),
                        "base_template": "base_partial.html",
                    },
                )
                msg = f"""<div id="party_content">
                        {template_string}
                    </div>
                    """
                async_to_sync(self.channel_layer.group_send)(
                    f"party_{party_id}", {"type": "html", "message": msg}
                )

    async def get_connected_players(self, group):
        assert self.channel_layer.valid_group_name(group), "Group name not valid"
        key = self.channel_layer._group_key(group)
        connection = self.channel_layer.connection(
            self.channel_layer.consistent_hash(group)
        )
        return await connection.zrange(key, 0, -1)

    def party_join(self, event):
        party_id = event["party_id"]
        logger.info(f"player joining to party {party_id=}")
        self.channel_layer.send(
            f"party_players_{party_id}",
            {
                "hola": "mundo",
                "date": datetime.datetime.now().isoformat(),
                "event": event,
            },
        )
