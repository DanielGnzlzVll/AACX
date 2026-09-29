from django import template

from core.models import UserRoundAnswer

register = template.Library()


@register.simple_tag
def answer_categories():
    return UserRoundAnswer.Category


@register.filter
def answer_for(round, category):
    return round.get(category, "")
