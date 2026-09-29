# -*- coding: utf-8 -*-
from django.contrib import admin

from .models import AnswerVerdict, Party, PartyRound, UserRoundAnswer


@admin.register(Party)
class PartyAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "status", "started_at", "closed_at")
    list_filter = ("status", "started_at", "closed_at")
    search_fields = ("name",)


class UserRoundAnswerInline(admin.TabularInline):
    model = UserRoundAnswer
    extra = 0

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        formset.form.base_fields["user"].initial = request.user
        return formset


@admin.register(PartyRound)
class PartyRoundAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "party",
        "number",
        "letter",
        "started_at",
        "closed_at",
        "closed_reason",
    )
    list_filter = ("party", "started_at", "closed_at", "closed_reason")

    inlines = [UserRoundAnswerInline]


@admin.register(UserRoundAnswer)
class UserRoundAnswerAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "round",
        "user",
        "field",
        "value",
        "scored_points",
        "verdict",
        "saved_at",
    )
    list_filter = ("round", "user", "verdict", "saved_at")


@admin.register(AnswerVerdict)
class AnswerVerdictAdmin(admin.ModelAdmin):
    list_display = ("id", "field", "value", "is_valid", "source", "created_at")
    list_filter = ("field", "is_valid", "source")
    search_fields = ("value",)
