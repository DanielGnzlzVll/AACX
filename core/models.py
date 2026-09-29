import collections
import random
import string
from collections import defaultdict
from itertools import groupby

from asgiref.sync import async_to_sync, sync_to_async
from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.db.models.functions import Coalesce, Lower
from django.utils import timezone

DUPLICATE_OPEN_PARTY_NAME_MESSAGE = "Ya existe una partida abierta con ese nombre."


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

    min_players = models.IntegerField(
        default=2, blank=True, null=True, validators=[MinValueValidator(2)],
        help_text="The minimum number of players required to start the game."
    )
    max_round_duration = models.IntegerField(
        default=120, blank=True, null=True, validators=[MinValueValidator(30)],
        help_text="The maximum duration of a round in seconds."
    )
    max_rounds = models.IntegerField(
        default=5,
        validators=[
            MinValueValidator(1),
            MaxValueValidator(len(string.ascii_uppercase)),
        ],
        help_text="The maximum number of rounds."
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

    get_answers_for_user = async_to_sync(aget_answers_for_user)
    get_current_or_next_round = async_to_sync(aget_current_or_next_round)
    get_current_round = async_to_sync(aget_current_round)
    get_players_scores = async_to_sync(aget_players_scores)
    get_winners = async_to_sync(aget_winners)


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

        for field, answers in answers_by_field.items():
            all_users_for_field_answers = defaultdict(int)
            for answer in answers:
                all_users_for_field_answers[answer.value] += 1

            for answer in answers:
                if not answer.value:
                    answers_to_save.append(answer)
                    continue
                if not answer.value.lower().startswith(self.letter.lower()):
                    answers_to_save.append(answer)
                    continue
                answer.scored_points = 100 // all_users_for_field_answers[answer.value]
                answers_to_save.append(answer)

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
