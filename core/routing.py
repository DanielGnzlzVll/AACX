from django.urls import re_path, path

from . import consumers

websocket_urlpatterns = [
    path("party/<int:party_id>/", consumers.PartyConsumer.as_asgi()),
]