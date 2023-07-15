from django.db import models

# Create your models here.

from django.conf import settings
from django.utils import timezone


class Party(models.Model):

    name = models.CharField(max_length=50)

    started_at = models.DateTimeField(blank=True, null=True)
    closed_at = models.DateTimeField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def is_active(self):
        return self.closed_at is None or self.closed_at <= timezone.now()


class PartyRound(models.Model):

    party = models.ForeignKey(Party, on_delete=models.CASCADE)
    letter = models.CharField(max_length=1)

    started_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("party", "letter")


class UserRoundAnswer(models.Model):

    NAME_CHOICE = "name"
    COUNTRY_CHOICE = "country"
    CITY_CHOICE = "city"
    COLOR_CHOICE = "COLOR"
    THING_CHOICE = "thing"
    ANIMAL_CHOICE = "animal"

    FIELD_CHOICES = (
        (NAME_CHOICE, NAME_CHOICE),
        (COUNTRY_CHOICE, COUNTRY_CHOICE),
        (CITY_CHOICE, CITY_CHOICE),
        (COLOR_CHOICE, COLOR_CHOICE),
        (THING_CHOICE, THING_CHOICE),
        (ANIMAL_CHOICE, ANIMAL_CHOICE),
    )

    round = models.ForeignKey(Party, on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    field = models.CharField(max_length=50, choices=FIELD_CHOICES)
    value = models.CharField(max_length=50)

    saved_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("round", "user", "field")
