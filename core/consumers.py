import time

from asgiref.sync import async_to_sync
from channels.generic.websocket import WebsocketConsumer


class PartyConsumer(WebsocketConsumer):
    def connect(self):
        self.party_id = self.scope["url_route"]["kwargs"]["party_id"]
        self.party_group_name = "party_%s" % self.party_id
        async_to_sync(self.channel_layer.group_add)(
            self.party_group_name, self.channel_name
        )
        self.accept()

    def receive(self, text_data):
        self.send(text_data=text_data)
        
    def html(self, event):
        self.send(text_data=event['message'])

    def disconnect(self, close_code):
        pass
