import asyncio
import collections
import datetime
import json
import logging

from asgiref.sync import async_to_sync, sync_to_async
from channels.generic.websocket import AsyncConsumer, AsyncWebsocketConsumer
from django.db import transaction
from django.template.loader import render_to_string

from core import forms, models

logger = logging.getLogger(__name__)
STATE_MACHINE_CHANNEL_NAME = "party-state-machine"


class PartyConsumerMixin:
    def get_party_group_name(
        self, *, party: models.Party | None, party_id: int | None
    ) -> str:
        assert party_id or party, "either party or party_id must be provided"
        if party:
            return "party_%s" % party.id
        return "party_%s" % party_id

    def get_party_player_connected_channel_name(
        self, *, party: models.Party | None, party_id: int | None
    ) -> str:
        assert party_id or party, "either party or party_id must be provided"
        if party:
            return "party_players_%s" % party.id
        return "party_players_%s" % party_id


class PartyConsumer(AsyncWebsocketConsumer, PartyConsumerMixin):
    async def connect(self):
        self.party_id = self.scope["url_route"]["kwargs"]["party_id"]
        user = self.scope["user"]
        await self.accept()
        logger.info(f"player connected to party: {self.party_id} {user.username=}")

        self.party_group_name = self.get_party_group_name(party_id=self.party_id)
        await self.channel_layer.group_add(self.party_group_name, self.channel_name)

        await self.channel_layer.send(
            self.get_party_player_connected_channel_name(party_id=self.party_id),
            {
                "hola": "mundo",
                "date": datetime.datetime.now().isoformat(),
                "party_id": self.party_id,
                "username": user.username,
                "user_id": user.id,
            },
        )

        self.party = await models.Party.objects.aget(id=self.party_id)

        if not self.party.started_at:
            logger.info(f"party no started yet {self.party_id=}")
            await self.channel_layer.send(
                STATE_MACHINE_CHANNEL_NAME,
                {
                    "type": "event_party_started",
                    "party_name": self.party.name,
                    "party_id": self.party.id,
                },
            )
            await self.send(text_data="waiting for players to join")

    async def receive(self, text_data):
        logger.info(f"receive {text_data=}")
        data = json.loads(text_data)
        if data["HEADERS"]["HX-Trigger"] == "party_current_answers_form":
            if await self.party_is_available():
                current_round = await self.party.get_current_round()
                form = forms.CurrentAnswersForm(
                    data,
                    current_round=current_round,
                )
                form.is_valid()
                self.form = form
                await self.save_form(form, current_round)
                if form.is_valid() and form.cleaned_data["submit_stop"]:
                    await self.channel_layer.group_send(
                        self.party_group_name,
                        {
                            "type": "event_party_round_stopped",
                        },
                    )
                    all_users_answers = (
                        await current_round.close_round_and_calculate_scores()
                    )
                    await self.display_all_answers(all_users_answers, current_round)
                    return
                template_string = render_to_string(
                    "party_current_answers.html",
                    {
                        "party": self.party,
                        "current_round": current_round,
                        "form": form,
                    },
                )
                await self.html({"message": template_string})

    async def html(self, event):
        await self.send(text_data=event["message"])

    async def party_round_stopped(self, event):
        logger.info(f"round stopped {self.party_id=}")
        current_round = await self.party.get_current_round()
        template_string = render_to_string(
            "party_current_answers.html",
            {
                "party": self.party,
                "current_round": current_round,
                "form": forms.CurrentAnswersForm(
                    current_round=current_round,
                    disabled=True,
                    # TODO: read from db and avoid using instance attributes
                    initial=self.form.cleaned_data,
                ),
                "disabled": True,
            },
        )
        await self.html({"message": template_string})

    async def disconnect(self, close_code):
        logger.info(
            "player disconnected from party: "
            f"{self.party_id} {self.scope['user'].username=}"
        )

    async def party_is_available(self):
        current_round = await self.party.get_current_round()
        if current_round.closed_at is None:
            return True
        return False

    async def save_form(self, form, current_round):
        data = {
            field: value
            for field, value in form.cleaned_data.items()
            if field in dict(models.UserRoundAnswer.FIELD_CHOICES)
        }

        await current_round.save_user_answers(self.scope["user"], data.items())

    async def display_all_answers(self, answers, current_round):
        await self.channel_layer.send(
            STATE_MACHINE_CHANNEL_NAME,
            {
                "type": "event_display_all_answers",
                "answers": answers,
                "current_round": current_round,
                "party_id": self.party_id,
            },
        )

    async def event_defer_group_html(self, event):
        sleep = event.pop("sleep", 0)
        await asyncio.sleep(sleep)
        await self.html(event)


class PartyStateMachine(AsyncConsumer, PartyConsumerMixin):
    async def event_party_started(self, event):
        party_id = event["party_id"]
        logger.info(f"starting {party_id=}")
        party = await self.handle_transaction_wait_players_to_join(
            party_id
        )

        await self.new_round(party)

        for i in range(2):
            await self.channel_layer.receive(f"party_new_round_{party_id}")
            await self.update_scores()
            await self.new_round(party)

        await self.update_scores()

    @sync_to_async
    def handle_transaction_wait_players_to_join(self, party_id):
        # should be sync code since django does not support async transactions
        with transaction.atomic():
            for party in models.Party.objects.select_for_update(
                skip_locked=True
            ).filter(id=party_id, started_at=None):
                async_to_sync(self.ensure_players_join)(party)
                logger.info("all players joined")
                party.started_at = datetime.datetime.now()
                party.save()
        return party

    async def ensure_players_join(self, party):
        logger.info("---- waiting players to join")
        for i in range(2):
            logger.info("---- waiting new player to join")
            player_data = await self.channel_layer.receive(
                self.get_party_player_connected_channel_name(party=party)
            )
            await party.joined_users.aadd(player_data["user_id"])

            current_players = await self.get_connected_players(
                self.get_party_group_name(party=party)
            )
            msg = f"""<div id="party_content">
                Esperando Mas Jugadores...
                Actualmente hay {len(current_players)} jugadores
            </div>
            """
            await self.channel_layer.group_send(
                self.get_party_group_name(party=party), {"type": "html", "message": msg}
            )

            logger.info(f"player joined {player_data=}")

    async def update_scores(self):
        pass

    async def new_round(self, party):
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
        await self.channel_layer.group_send(
            self.get_party_group_name(party=party), {"type": "html", "message": msg}
        )

    async def get_connected_players(self, group):
        assert self.channel_layer.valid_group_name(group), "Group name not valid"
        key = self.channel_layer._group_key(group)
        connection = self.channel_layer.connection(
            self.channel_layer.consistent_hash(group)
        )
        return await connection.zrange(key, 0, -1)

    async def event_party_join(self, event):
        party_id = event["party_id"]
        logger.info(f"player joining to party {party_id=}")
        self.channel_layer.send(
            self.get_party_player_connected_channel_name(party_id=party_id),
            {
                "hola": "mundo",
                "date": datetime.datetime.now().isoformat(),
                "event": event,
            },
        )

    async def event_display_all_answers(self, event):
        answers = event["answers"]
        current_round = event["current_round"]
        party = event["party"]
        await self.display_all_answers(answers, current_round, party)

    async def display_all_answers(self, answers, current_round, party):
        grouped_answers = collections.defaultdict(list)

        for answer in answers:
            grouped_answers[answer.field].append(
                {
                    "value": answer.value,
                    "scored_points": answer.scored_points,
                    "username": await sync_to_async(lambda: answer.user.username)(),
                }
            )

        times = [0.5] + [2] * len(models.UserRoundAnswer.FIELD_CHOICES)

        for field, _ in models.UserRoundAnswer.FIELD_CHOICES:
            answers = grouped_answers[field]
            template_string = render_to_string(
                "party_current_all_users_answers_modal.html",
                {
                    "party": self.party,
                    "current_round": current_round,
                    "answers": answers,
                    "field": field,
                    "open": "open",
                },
            )

            await self.channel_layer.group_send(
                self.get_party_group_name(party=party),
                {
                    "type": "event_defer_group_html",
                    "sleep": times.pop(0),
                    "message": template_string,
                },
            )

        template_string = render_to_string(
            "party_current_all_users_answers_modal.html",
            {"open": ""},
        )

        await self.channel_layer.group_send(
            self.get_party_group_name(party=party),
            {
                "type": "event_defer_group_html",
                "sleep": times.pop(0),
                "message": template_string,
            },
        )
