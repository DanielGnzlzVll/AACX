import collections
import datetime
import enum
import random
import string
import unicodedata
from collections import defaultdict
from itertools import groupby

from asgiref.sync import async_to_sync, sync_to_async
from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.db.models.functions import Coalesce, Lower
from django.utils import timezone

DUPLICATE_OPEN_PARTY_NAME_MESSAGE = "Ya existe una partida abierta con ese nombre."

COMBINING_TILDE = "\u0303"


def normalize_answer(value):
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    # Ñ is its own letter in Spanish, so its tilde is the one mark kept.
    kept = "".join(
        char
        for previous, char in zip(" " + decomposed, decomposed)
        if not unicodedata.combining(char)
        or (char == COMBINING_TILDE and previous == "n")
    )
    return " ".join(unicodedata.normalize("NFC", kept).split())


def answer_starts_with(value, letter):
    return normalize_answer(value).startswith(normalize_answer(letter))


class PartyAccess(enum.Enum):
    WAITING = "waiting"
    PARTICIPANT = "participant"
    STARTED = "started"
    CLOSED = "closed"

    @property
    def can_play(self):
        return self in (PartyAccess.WAITING, PartyAccess.PARTICIPANT)


class PartyQuerySet(models.QuerySet):
    def get_available_parties(self, user):
        return self.filter(
            started_at__isnull=True
        ).order_by("-pk") | self.filter(
            closed_at__isnull=True, joined_users__pk=user.id
        )


class Party(models.Model):
    name = models.CharField(max_length=50)

    waiting_started_at = models.DateTimeField(blank=True, null=True)
    started_at = models.DateTimeField(blank=True, null=True)
    closed_at = models.DateTimeField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="created_parties",
    )

    joined_users = models.ManyToManyField(
        settings.AUTH_USER_MODEL, related_name="parties"
    )

    min_players = models.PositiveSmallIntegerField(
        "mínimo de jugadores",
        default=2,
        validators=[MinValueValidator(2), MaxValueValidator(20)],
        help_text="Jugadores necesarios para empezar la partida (entre 2 y 20).",
    )
    max_round_duration = models.PositiveSmallIntegerField(
        "duración máxima de la ronda",
        default=120,
        validators=[MinValueValidator(30), MaxValueValidator(600)],
        help_text="Duración máxima de cada ronda en segundos (entre 30 y 600).",
    )
    max_rounds = models.PositiveSmallIntegerField(
        "número de rondas",
        default=5,
        validators=[
            MinValueValidator(1),
            MaxValueValidator(len(string.ascii_uppercase)),
        ],
        help_text="Número de rondas de la partida (entre 1 y 26).",
    )

    objects = PartyQuerySet.as_manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                Lower("name"),
                condition=models.Q(closed_at__isnull=True),
                name="unique_open_party_name",
                violation_error_message=DUPLICATE_OPEN_PARTY_NAME_MESSAGE,
            ),
        ]

    def __str__(self):
        return self.name

    @property
    def is_active(self):
        return self.closed_at is None

    async def aget_access(self, user):
        if await self.joined_users.filter(pk=user.pk).aexists():
            return PartyAccess.PARTICIPANT
        if self.closed_at:
            return PartyAccess.CLOSED
        if self.started_at:
            return PartyAccess.STARTED
        return PartyAccess.WAITING

    async def aget_current_or_next_round(self):
        current = await self.aget_current_round()
        if current and current.closed_at is None:
            return current
        letter = random.choice(string.ascii_uppercase)
        parties_letters = {
            letter
            async for letter in PartyRound.objects.filter(party_id=self.id).values_list(
                "letter", flat=True
            )
        }
        left_letters = set(list(string.ascii_uppercase))
        left_letters = left_letters.difference(parties_letters)
        if not left_letters:
            raise Exception("All letters are used")
        letter = random.choice(list(left_letters))
        return await PartyRound.objects.acreate(
            party=self,
            letter=letter,
            started_at=timezone.now(),
        )

    async def aget_current_round(self):
        round = await (
            PartyRound.objects.filter(party_id=self.id).order_by("-started_at").afirst()
        )
        return round

    async def aget_players_scores(
        self,
    ):
        points_grouped = (
            UserRoundAnswer.objects.filter(round__party_id=self.id)
            .values("user__username")
            .annotate(scored_points=Coalesce(models.Sum("scored_points"), 0))
            .order_by("-scored_points")
            .values_list("user__username", "scored_points")
        )
        return {username: points async for username, points in points_grouped}

    async def aget_winners(self):
        scores = await self.aget_players_scores()
        if not scores:
            return []
        best = max(scores.values())
        if not best:
            return []
        return [username for username, points in scores.items() if points == best]

    async def acount_connected_players(self):
        return (
            await PartyConnection.objects.alive()
            .filter(party_id=self.id)
            .values("user_id")
            .distinct()
            .acount()
        )

    async def aget_answers_for_user(self, user, closed_rounds_only=False):
        answers = UserRoundAnswer.objects.filter(
            user_id=user.id,
            round__party_id=self.id,
        )
        if closed_rounds_only:
            answers = answers.filter(round__closed_at__isnull=False)
        answers_dict = [
            round
            async for round in answers.order_by("round").values(
                "field", "value", "round__letter"
            )
        ]

        answerlist = []
        for letter, answers in groupby(answers_dict, lambda x: x["round__letter"]):
            answerlist.append(
                {"letter": letter}
                | {answer["field"]: answer["value"] for answer in answers}
            )

        return answerlist

    get_access = async_to_sync(aget_access)
    get_answers_for_user = async_to_sync(aget_answers_for_user)
    get_current_round = async_to_sync(aget_current_round)
    get_players_scores = async_to_sync(aget_players_scores)
    get_winners = async_to_sync(aget_winners)


class PartyConnectionQuerySet(models.QuerySet):
    def alive(self):
        return self.filter(last_seen_at__gte=timezone.now() - PartyConnection.TTL)

    def stale(self):
        return self.filter(last_seen_at__lt=timezone.now() - PartyConnection.TTL)


class PartyConnection(models.Model):
    HEARTBEAT_INTERVAL = datetime.timedelta(seconds=20)
    TTL = 3 * HEARTBEAT_INTERVAL

    party = models.ForeignKey(
        Party, on_delete=models.CASCADE, related_name="connections"
    )
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    channel_name = models.CharField(max_length=255, unique=True)
    last_seen_at = models.DateTimeField()

    objects = PartyConnectionQuerySet.as_manager()

    def __str__(self):
        return f"{self.party} - {self.user}"


class PartyRoundQuerySet(models.QuerySet):
    async def aclose(self):
        return await self.filter(closed_at__isnull=True).aupdate(
            closed_at=timezone.now()
        )


class PartyRound(models.Model):
    party = models.ForeignKey(Party, on_delete=models.CASCADE)
    letter = models.CharField(max_length=1)

    started_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)

    objects = PartyRoundQuerySet.as_manager()

    class Meta:
        unique_together = ("party", "letter")

    def __str__(self):
        return f"{self.party} - {self.letter}"

    async def close(self):
        closed = await PartyRound.objects.filter(pk=self.pk).aclose()
        if closed:
            await self.arefresh_from_db(fields=["closed_at"])
        return bool(closed)

    async def save_user_answers(self, user, answers):
        answers_list = []
        for field, value in answers:
            answers_list.append(
                UserRoundAnswer(
                    round=self,
                    user=user,
                    field=field,
                    value=value,
                )
            )
        await UserRoundAnswer.objects.abulk_create(
            answers_list,
            update_conflicts=True,
            update_fields=["value"],
            unique_fields=["round", "user", "field"],
        )

    async def close_round_and_calculate_scores(self):
        return await sync_to_async(self._close_round_and_calculate_scores)()

    @transaction.atomic
    def _close_round_and_calculate_scores(self):
        PartyRound.objects.filter(pk=self.pk, closed_at__isnull=True).update(
            closed_at=timezone.now()
        )
        self.refresh_from_db(fields=["closed_at"])

        answers_to_save = []
        answers_by_field = collections.defaultdict(list)
        for answer in UserRoundAnswer.objects.filter(round=self):
            answers_by_field[answer.field].append(answer)

        letter = normalize_answer(self.letter)
        for answers in answers_by_field.values():
            valid_answers = defaultdict(list)
            for answer in answers:
                answer.scored_points = 0
                normalized = normalize_answer(answer.value)
                if normalized.startswith(letter):
                    valid_answers[normalized].append(answer)
                answers_to_save.append(answer)

            for same_answers in valid_answers.values():
                for answer in same_answers:
                    answer.scored_points = 100 // len(same_answers)

        UserRoundAnswer.objects.bulk_update(answers_to_save, ["scored_points"])

        closed_rounds = PartyRound.objects.filter(
            party_id=self.party_id, closed_at__isnull=False
        ).count()
        if closed_rounds >= self.party.max_rounds:
            Party.objects.filter(id=self.party_id, closed_at__isnull=True).update(
                closed_at=self.closed_at
            )
        return answers_to_save

    async def aget_initial_data_for_user(self, user):
        return {
            answer.field: answer.value
            async for answer in UserRoundAnswer.objects.filter(round=self, user=user)
        }


class UserRoundAnswer(models.Model):
    NAME_CHOICE = "name"
    LAST_NAME_CHOICE = "last_name"
    COUNTRY_CHOICE = "country"
    CITY_CHOICE = "city"
    COLOR_CHOICE = "color"
    THING_CHOICE = "thing"
    ANIMAL_CHOICE = "animal"

    FIELD_CHOICES = (
        (NAME_CHOICE, NAME_CHOICE),
        (LAST_NAME_CHOICE, LAST_NAME_CHOICE),
        (COUNTRY_CHOICE, COUNTRY_CHOICE),
        (CITY_CHOICE, CITY_CHOICE),
        (ANIMAL_CHOICE, ANIMAL_CHOICE),
        (THING_CHOICE, THING_CHOICE),
        (COLOR_CHOICE, COLOR_CHOICE),
    )

    round = models.ForeignKey(PartyRound, on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    field = models.CharField(max_length=50, choices=FIELD_CHOICES)
    value = models.CharField(max_length=50)

    scored_points = models.IntegerField(null=True, blank=True)

    saved_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("round", "user", "field")

    def __str__(self):
        return f"{self.round} - {self.user} - {self.field} - {self.value}"
