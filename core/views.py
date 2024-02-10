import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.models import User
from django.http import Http404
from django.views import View
from django.views.generic.base import ContextMixin, TemplateResponseMixin

from core import models, forms

logger = logging.getLogger(__name__)


class HTMXPartialMixin(ContextMixin, TemplateResponseMixin):
    def get_context_data(self, *args, **kwargs):
        context = super().get_context_data(*args, **kwargs)
        if self.request.htmx:
            context.setdefault("base_template", "base_partial.html")
        else:
            context.setdefault("base_template", "base.html")
        return context


class Login(
    HTMXPartialMixin,
    View,
):
    def get_template_names(self):
        if self.request.method == "POST":
            return ["home.html"]
        return ["login.html"]

    def get(self, request, *args, **kwargs):
        context = self.get_context_data(**kwargs)
        return self.render_to_response(context)

    def post(self, request, *args, **kwargs):
        username = request.POST["nickname"]
        user, _ = User.objects.get_or_create(username=username)
        login(request, user)
        # TODO: try a redirect
        context = self.get_context_data(**kwargs)
        context["parties"] = models.Party.objects.all()
        return self.render_to_response(context)


class Home(
    LoginRequiredMixin,
    HTMXPartialMixin,
    View,
):
    template_name = "home.html"

    def get_context_data(self, *args, **kwargs):
        context = super().get_context_data(*args, **kwargs)
        context["parties"] = models.Party.objects.filter(
            started_at__isnull=True
        ).order_by("-pk") | models.Party.objects.filter(
            closed_at__isnull=True, joined_users__pk=self.request.user.id
        )
        return context

    def get(self, request, *args, **kwargs):
        context = self.get_context_data(**kwargs)
        return self.render_to_response(context)


class CreateParty(LoginRequiredMixin, HTMXPartialMixin, View):
    def get_template_names(self):
        if self.request.method == "POST":
            return ["home.html"]
        return ["create_party.html"]

    def get(self, request, *args, **kwargs):
        context = self.get_context_data(**kwargs)
        return self.render_to_response(context)

    def post(self, request, *args, **kwargs):
        name = request.POST.get("party_name")
        party, created = models.Party.objects.get_or_create(name=name)
        if created:
            messages.add_message(
                request, messages.SUCCESS, f"'{party.name}' created successfully."
            )

        context = self.get_context_data(**kwargs)
        context["parties"] = models.Party.objects.filter(
            started_at__isnull=True
        ).order_by("-pk")
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.send)(
            "party_state_machine",
            {
                "type": "party_stared",
                "party_name": party.name,
                "party_id": party.id,
            },
        )
        return self.render_to_response(context)


class DetailParty(LoginRequiredMixin, HTMXPartialMixin, View):
    template_name = "party_no_started.html"

    def get_context_data(self, *args, **kwargs):
        context = super().get_context_data(*args, **kwargs)
        party_qs = models.Party.objects.filter(id=kwargs["party_id"]) & (
            models.Party.objects.filter(started_at__isnull=True)
            | models.Party.objects.filter(joined_users__pk=self.request.user.id)
        )
        if not party_qs.exists():
            raise Http404()

        context["party"] = party_qs.get()
        self.party = context["party"]
        context["current_round"] = self.party.get_current_or_next_round()
        context["players_scores"] = self.party.get_players_scores()
        context["rounds"] = self.party.get_answers_for_user(self.request.user)
        context["form"] = forms.CurrentAnswersForm(
            current_round=context["current_round"],
        )
        return context

    def get_template_names(self):
        if self.party.started_at:
            return ["party.html"]
        return ["party_no_started.html"]

    def get(self, request, *args, **kwargs):
        context = self.get_context_data(**kwargs)
        return self.render_to_response(context)
