import logging
import time

from asgiref.sync import async_to_sync
from channels.generic.websocket import SyncConsumer, WebsocketConsumer
from channels.layers import get_channel_layer

logger = logging.getLogger(__name__)


class PartyConsumer(WebsocketConsumer):
    def connect(self):
        self.party_id = self.scope["url_route"]["kwargs"]["party_id"]
        self.party_group_name = "party_%s" % self.party_id
        async_to_sync(self.channel_layer.group_add)(
            self.party_group_name, self.channel_name
        )
        logger.info(f"player connects {self.party_id}")
        async_to_sync(self.channel_layer.send)(
            f"party_{self.party_id}_join",
            {"type": "party.join"}
        )
        self.accept()

    def receive(self, text_data):
        pass
        #self.send(text_data=text_data)

    def html(self, event):
        self.send(text_data=event['message'])

    def disconnect(self, close_code):
        pass


class PartyStateMachine(SyncConsumer):
    def party_started(self, event):
        logger.critical("party started")
        channel_layer = get_channel_layer()
        party_id = event["party_id"]
        for i in range(67, 91):
            async_to_sync(channel_layer.group_send)(f"party_{party_id}", {
                "type": "html",
                "message": f"""
                <div id="current_round_letter">
                    <h3>{ chr(i) }</h3>
                </div>
                """
            }
            )
            time.sleep(5)

    def party_join(self, event):
        logger.critical("party started 2")
        channel_layer = get_channel_layer()
        party_id = event["party_id"]
        for _ in range(2):
            player_event = async_to_sync(channel_layer.receive)("party_join_test")
            logger.info(f"player join {player_event=}")
        logger.info("all players joined")
