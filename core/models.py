import collections
import random
import string
from collections import defaultdict
from itertools import groupby

from asgiref.sync import async_to_sync, sync_to_async
from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone


class PartyQuerySet(models.QuerySet):
    def get_available_parties(self, user):
        return self.filter(
            started_at__isnull=True
        ).order_by("-pk") | self.filter(
            closed_at__isnull=True, joined_users__pk=user.id
        )


class Party(models.Model):
    name = models.CharField(max_length=50)

    started_at = models.DateTimeField(blank=True, null=True)
    closed_at = models.DateTimeField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)

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
        blank=True,
        null=True,
        validators=[
            MinValueValidator(1),
            MaxValueValidator(len(string.ascii_uppercase)),
        ],
        help_text="The maximum number of rounds."
    )

    objects = PartyQuerySet.as_manager()

    def __str__(self):
        return self.name

    @property
    def is_active(self):
        return self.closed_at is None or self.closed_at <= timezone.now()

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
            .annotate(scored_points=models.Sum("scored_points"))
            .order_by("-scored_points")
            .values_list("user__username", "scored_points")
        )
        return {username: points async for username, points in points_grouped}

    async def aget_answers_for_user(self, user):
        # UserRoundAnswer.objects.filter(user=user, round__party_id=self.id)
        answers_dict = [
            round
            async for round in UserRoundAnswer.objects.all()
            .order_by("round")
            .values("field", "value", "round__letter")
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


class PartyRound(models.Model):
    party = models.ForeignKey(Party, on_delete=models.CASCADE)
    letter = models.CharField(max_length=1)

    started_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("party", "letter")

    def __str__(self):
        return f"{self.party} - {self.letter}"

    async def close(self):
        self.closed_at = timezone.now()
        await self.asave()

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
        await self.close()

        answers_to_save = []
        answers_by_field = collections.defaultdict(list)
        async for answer in UserRoundAnswer.objects.filter(round=self):
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

        await UserRoundAnswer.objects.abulk_update(answers_to_save, ["scored_points"])
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
