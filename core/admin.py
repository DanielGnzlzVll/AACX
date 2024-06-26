# -*- coding: utf-8 -*-
from django.contrib import admin

from .models import Party, PartyRound, UserRoundAnswer


@admin.register(Party)
class PartyAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'started_at', 'closed_at')
    list_filter = ('started_at', 'closed_at')
    search_fields = ('name',)


class UserRoundAnswerInline(admin.TabularInline):
    model = UserRoundAnswer
    extra = 0

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        formset.form.base_fields['user'].initial = request.user
        return formset


@admin.register(PartyRound)
class PartyRoundAdmin(admin.ModelAdmin):
    list_display = ('id', 'party', 'letter', 'started_at', 'closed_at')
    list_filter = ('party', 'started_at', 'closed_at')

    inlines = [UserRoundAnswerInline]



@admin.register(UserRoundAnswer)
class UserRoundAnswerAdmin(admin.ModelAdmin):
    list_display = ('id', 'round', 'user', 'field', 'value', 'scored_points', 'saved_at')
    list_filter = ('round', 'user', 'saved_at')
