import random
import string
from collections import defaultdict
from itertools import groupby

from django.conf import settings
from django.db import models
from django.utils import timezone

from asgiref.sync import async_to_sync, sync_to_async


class Party(models.Model):
    name = models.CharField(max_length=50)

    started_at = models.DateTimeField(blank=True, null=True)
    closed_at = models.DateTimeField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)

    joined_users = models.ManyToManyField(
        settings.AUTH_USER_MODEL, related_name="parties"
    )

    def __str__(self):
        return self.name

    @property
    def is_active(self):
        return self.closed_at is None or self.closed_at <= timezone.now()

    def get_current_or_next_round(self):
        current = async_to_sync(self.get_current_round)()
        if current.closed_at is None:
            return current
        letter = random.choice(string.ascii_uppercase)
        while PartyRound.objects.filter(party_id=self.id, letter=letter).exists():
            letter = random.choice(string.ascii_uppercase)
        return PartyRound.objects.create(
            party=self,
            letter=letter,
            started_at=timezone.now(),
        )

    async def get_current_round(self):
        round = await (
            PartyRound.objects.filter(party_id=self.id).order_by("-started_at").afirst()
        )
        return round

    def get_players_scores(self):
        scores = defaultdict(float)

        for round in self.partyround_set.filter(closed_at__isnull=True):
            user_answer = defaultdict(lambda: defaultdict(str))
            for answer in round.userroundanswer_set.all().order_by("field"):
                user_answer[answer.field][answer.user.username] = (
                    answer.value
                    if answer.value.lower().startswith(round.letter.lower())
                    else ""
                )

            for field in user_answer:
                seen = set()
                for username in user_answer[field]:
                    if user_answer[field][username]:
                        seen.add(user_answer[field][username])

                for username in user_answer[field]:
                    if not user_answer[field][username]:
                        continue
                    if user_answer[field][username] in seen:
                        scores[username] += 1
                        continue
                    scores[username] += 2

        scores = {
            "Dani": 25,
            "Juancho": int(random.random() * 24),
            "Gonzalo": int(random.random() * 24),
            "Lucas": int(random.random() * 24),
        }
        return dict(sorted(scores.items(), key=lambda x: x[1], reverse=True))

    def get_answers_for_user(self, user):
        # UserRoundAnswer.objects.filter(user=user, round__party_id=self.id)
        answers_dict = list(
            UserRoundAnswer.objects.all()
            .order_by("round")
            .values("field", "value", "round__letter")
        )

        answerlist = []
        for letter, answers in groupby(answers_dict, lambda x: x["round__letter"]):
            answerlist.append(
                {"letter": letter} | {answer["field"]: answer["value"] for answer in answers}
            )

        return answerlist


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
        await self.save()

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

    saved_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("round", "user", "field")

    def __str__(self):
        return f"{self.round} - {self.user} - {self.field} - {self.value}"
