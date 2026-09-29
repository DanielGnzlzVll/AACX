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
        self, *, party: models.Party | None = None, party_id: int | None = None
    ) -> str:
        assert party_id or party, "either party or party_id must be provided"
        if party:
            return "party_%s" % party.id
        return "party_%s" % party_id

    def get_party_player_connected_channel_name(
        self, *, party: models.Party | None = None, party_id: int | None = None
    ) -> str:
        assert party_id or party, "either party or party_id must be provided"
        if party:
            return "party_players_%s" % party.id
        return "party_players_%s" % party_id


class PartyConsumer(AsyncWebsocketConsumer, PartyConsumerMixin):
    CLOSE_UNAUTHENTICATED = 4401
    CLOSE_FORBIDDEN = 4403
    CLOSE_PARTY_NOT_FOUND = 4404

    async def connect(self):
        self.party_id = self.scope["url_route"]["kwargs"]["party_id"]
        user = self.scope["user"]
        if not user.is_authenticated:
            await self.close(code=self.CLOSE_UNAUTHENTICATED)
            return
        try:
            self.party = await models.Party.objects.aget(id=self.party_id)
        except models.Party.DoesNotExist:
            await self.close(code=self.CLOSE_PARTY_NOT_FOUND)
            return
        if not await self.can_join(user):
            await self.close(code=self.CLOSE_FORBIDDEN)
            return

        await self.accept()
        logger.info(f"player connected to party: {self.party_id} {user.username=}")

        self.party_group_name = self.get_party_group_name(party_id=self.party_id)
        await self.channel_layer.group_add(self.party_group_name, self.channel_name)

        await self.channel_layer.send(
            self.get_party_player_connected_channel_name(party_id=self.party_id),
            {
                "date": datetime.datetime.now().isoformat(),
                "party_id": self.party_id,
                "username": user.username,
                "user_id": user.id,
            },
        )

        if not self.party.closed_at:
            logger.info(f"party no finalized yet {self.party_id=} trying to start")
            await self.channel_layer.send(
                STATE_MACHINE_CHANNEL_NAME,
                {
                    "type": "event_party_started",
                    "party_name": self.party.name,
                    "party_id": self.party.id,
                },
            )

    async def can_join(self, user):
        return self.party.started_at is None or await self.is_participant(user)

    async def is_participant(self, user):
        return await self.party.joined_users.filter(pk=user.pk).aexists()

    async def receive(self, text_data=None, bytes_data=None):
        try:
            data = json.loads(text_data)
            trigger = data["HEADERS"]["HX-Trigger"]
        except (TypeError, ValueError, KeyError):
            logger.warning(f"ignoring malformed message {self.party_id=}")
            return
        if trigger == "party_current_answers_form":
            await self.handle_form_submit(data)
        else:
            logger.warning(f"ignoring unknown message {self.party_id=} {trigger=}")

    async def handle_form_submit(self, form_data):
        if not await self.is_participant(self.scope["user"]):
            logger.info(f"skipping form submit from non-participant {self.party_id=}")
            return
        if not await self.party_is_available():
            logger.info(f"skipping form submit {form_data=} since party is closed")
            return
        current_round = await self.party.aget_current_round()
        form = forms.CurrentAnswersForm(
            form_data,
            current_round=current_round,
        )
        form.is_valid()
        self.form = form
        await self.save_form(form, current_round)
        if form.is_valid() and form.cleaned_data["submit_stop"]:
            await self.channel_layer.send(
                STATE_MACHINE_CHANNEL_NAME,
                {
                    "type": "event_party_round_stopped",
                    "party_id": self.party.id,
                    "round_id": current_round.id,
                },
            )
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

    async def event_party_round_stopped(self, event):
        logger.info(f"round stopped {self.party_id=}")
        current_round = await self.party.aget_current_round()
        template_string = render_to_string(
            "party_current_answers.html",
            {
                "party": self.party,
                "current_round": current_round,
                "form": forms.CurrentAnswersForm(
                    current_round=current_round,
                    disabled=True,
                    initial=await current_round.aget_initial_data_for_user(
                        self.scope["user"]
                    ),
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
        current_round = await self.party.aget_current_round()
        return current_round is not None and current_round.closed_at is None

    async def save_form(self, form, current_round):
        data = {
            field: value
            for field, value in form.cleaned_data.items()
            if field in dict(models.UserRoundAnswer.FIELD_CHOICES)
        }

        await current_round.save_user_answers(self.scope["user"], data.items())

    async def event_update_past_answers(self, event):
        rounds = await self.party.aget_answers_for_user(self.scope["user"])
        template_string = render_to_string(
            "party_answers.html", context={"rounds": rounds}
        )
        await self.html({"message": template_string})


class PartyStateMachine(AsyncConsumer, PartyConsumerMixin):

    MAX_WAITING_TIME = 120

    async def event_party_started(self, event):
        party_id = event["party_id"]
        force_start = event.get("force_start", False)
        party = await self.handle_transaction_wait_players_to_join(party_id)
        if not party and not force_start:
            logger.info("Party already locked so skipping")
            return
        elif not party and force_start:
            party = await models.Party.objects.aget(id=party_id)
        if party.closed_at:
            logger.info(f"party {party_id} already finished so skipping")
            return
        logger.info(f"starting {party_id=}")

        while not party.closed_at:
            current_round = await self.next_round(party)
            await self.wait_for_round_end(party, current_round)
            await self.update_scores(party, current_round)
            await party.arefresh_from_db(fields=["closed_at"])

        await self.finish_party(party)
        logger.info(f"party {party_id} finished")

    async def wait_for_round_end(self, party, current_round):
        try:
            async with asyncio.timeout(party.max_round_duration):
                while True:
                    message = await self.channel_layer.receive(
                        f"party_new_round_{party.id}"
                    )
                    if message.get("round_id") == current_round.id:
                        return
                    logger.info(f"ignoring stale round end {message=}")
        except TimeoutError:
            logger.info("timeout waiting for new round")

        if await current_round.close():
            await self.channel_layer.group_send(
                self.get_party_group_name(party=party),
                {"type": "event_party_round_stopped"},
            )

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
        timeout_task_name = "timeout"
        timeout_task = asyncio.create_task(
            asyncio.sleep(self.MAX_WAITING_TIME),
            name=timeout_task_name,
        )

        logger.info("---- waiting players to join")
        for _ in range(party.min_players):
            logger.info("---- waiting new player to join")

            receive_task = asyncio.create_task(
                self.channel_layer.receive(
                    self.get_party_player_connected_channel_name(party=party)
                ),
            )
            done, _ = await asyncio.wait(
                (timeout_task, receive_task),
                return_when=asyncio.FIRST_COMPLETED,
            )
            task_done = done.pop()
            if task_done.get_name() == timeout_task_name:
                logger.info("---- timeout waiting new player to join")
                break
            player_data = task_done.result()

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

    async def update_scores(self, party, current_round):
        all_users_answers = await current_round.close_round_and_calculate_scores()
        await self.display_all_answers(all_users_answers, current_round, party)
        await self.channel_layer.group_send(
            self.get_party_group_name(party=party),
            {"type": "event_update_past_answers"},
        )
        # TODO: update scores

    async def next_round(self, party):
        next_or_current_round = await party.aget_current_or_next_round()
        template_string = render_to_string(
            "_party_content.html",
            {
                "party": party,
                "players_scores": await party.aget_players_scores(),
                "current_round": next_or_current_round,
                "base_template": "base_partial.html",
                "form": forms.CurrentAnswersForm(
                    current_round=next_or_current_round, autofocus_name=True
                ),
            },
        )
        await self.channel_layer.group_send(
            self.get_party_group_name(party=party),
            {"type": "html", "message": template_string},
        )
        return next_or_current_round

    async def finish_party(self, party):
        template_string = render_to_string(
            "party_finished_update.html",
            {
                "party": party,
                "players_scores": await party.aget_players_scores(),
                "winners": await party.aget_winners(),
                "current_round": await party.aget_current_round(),
            },
        )
        await self.channel_layer.group_send(
            self.get_party_group_name(party=party),
            {"type": "html", "message": template_string},
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
                    "party": party,
                    "current_round": current_round,
                    "answers": answers,
                    "field": field,
                    "open": "open",
                },
            )
            await self.channel_layer.group_send(
                self.get_party_group_name(party=party),
                {
                    "type": "html",
                    "message": template_string,
                },
            )
            await asyncio.sleep(times.pop(0))

        template_string = render_to_string(
            "party_current_all_users_answers_modal.html",
            {"open": ""},
        )
        await self.channel_layer.group_send(
            self.get_party_group_name(party=party),
            {
                "type": "html",
                "message": template_string,
            },
        )
        await asyncio.sleep(times.pop(0))

    async def event_party_round_stopped(self, event):
        party_id, round_id = event["party_id"], event["round_id"]
        closed = await models.PartyRound.objects.filter(
            id=round_id, party_id=party_id
        ).aclose()
        if not closed:
            logger.info(f"ignoring stop for a closed round {party_id=} {round_id=}")
            return
        await self.channel_layer.group_send(
            self.get_party_group_name(party_id=party_id),
            {"type": "event_party_round_stopped"},
        )
        await self.channel_layer.send(
            f"party_new_round_{party_id}", {"round_id": round_id}
        )
