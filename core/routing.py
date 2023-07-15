from django.urls import path

from . import consumers

websocket_urlpatterns = [
    path("party/<int:party_id>/", consumers.PartyConsumer.as_asgi()),
]

channel_routing = {
    "party-state-machine": consumers.PartyStateMachine.as_asgi(),
}