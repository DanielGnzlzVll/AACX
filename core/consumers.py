import logging

import datetime

from channels.generic.websocket import (
    AsyncConsumer,
    AsyncWebsocketConsumer,
)

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
            },
        )

        party = await models.Party.objects.aget(id=self.party_id)

        if not party.started_at:
            await self.send(text_data="waiting for players to join")

    async def html(self, event):
        self.send(text_data=event["message"])

    async def disconnect(self, close_code):
        logger.info(
            f"player disconnected from party: {self.party_id} {self.scope['user'].username=}"
        )


class PartyStateMachine(AsyncConsumer):
    async def party_started(self, event):
        party_id = event["party_id"]
        logger.info(f"party started {party_id=}")
        logger.info("waiting for players to join")
        for i in range(2):
            logger.info("---- waiting new player to join")
            player_data = await self.channel_layer.receive(f"party_players_{party_id}")
            logger.info(f"player joined {player_data=}")

        logger.info("all players joined")

    async def get_connected_players(self, group):
        assert self.channel_layer.valid_group_name(group), "Group name not valid"
        key = self.channel_layer._group_key(group)
        connection = self.channel_layer.connection(
            self.channel_layer.consistent_hash(group)
        )
        return await connection.get(key)

    async def party_join(self, event):
        party_id = event["party_id"]
        logger.info(f"player joining to party {party_id=}")
        await self.channel_layer.send(
            f"party_players_{party_id}",
            {
                "hola": "mundo",
                "date": datetime.datetime.now().isoformat(),
                "event": event,
            },
        )
