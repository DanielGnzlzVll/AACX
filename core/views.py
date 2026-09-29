import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.http import Http404
from django.shortcuts import redirect
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.generic.base import ContextMixin, TemplateResponseMixin

from core import forms, models

logger = logging.getLogger(__name__)


class HTMXPartialMixin(ContextMixin, TemplateResponseMixin):
    def get_context_data(self, *args, **kwargs):
        context = super().get_context_data(*args, **kwargs)
        if self.request.htmx:
            context.setdefault("base_template", "base_partial.html")
        else:
            context.setdefault("base_template", "base.html")
        return context


NICKNAME_CLAIM_COOKIE = "aacx_nickname_claim"
NICKNAME_CLAIM_SALT = "core.views.Login.nickname_claim"
NICKNAME_CLAIM_MAX_AGE = 60 * 60 * 24 * 365
LOGIN_REJECTED_MESSAGE = "No es posible iniciar sesión con ese nombre de usuario."


class Login(
    HTMXPartialMixin,
    View,
):
    template_name = "login.html"

    def get(self, request, *args, **kwargs):
        context = self.get_context_data(**kwargs)
        context["form"] = forms.LoginForm()
        return self.render_to_response(context)

    def post(self, request, *args, **kwargs):
        form = forms.LoginForm(request.POST)
        if form.is_valid():
            user = self.get_nickname_user(form.cleaned_data["nickname"])
            if user is not None:
                login(request, user)
                response = redirect(self.get_success_url())
                response.set_signed_cookie(
                    NICKNAME_CLAIM_COOKIE,
                    str(user.pk),
                    salt=NICKNAME_CLAIM_SALT,
                    max_age=NICKNAME_CLAIM_MAX_AGE,
                    httponly=True,
                    samesite="Lax",
                )
                return response
            form.add_error(None, LOGIN_REJECTED_MESSAGE)

        context = self.get_context_data(**kwargs)
        context["form"] = form
        return self.render_to_response(context)

    def get_nickname_user(self, nickname):
        """Return the user for this nickname, or None if this browser may not use it.

        A nickname belongs to the browser that first claimed it, proven by a
        signed cookie. Staff, superusers and accounts with a password must use
        /admin/login/ instead.
        """
        user = User.objects.filter(username__iexact=nickname).order_by("pk").first()
        if user is None:
            user = User(username=nickname)
            user.set_unusable_password()
            try:
                with transaction.atomic():
                    user.save()
            except IntegrityError:
                return None
            return user

        if user.is_staff or user.is_superuser or user.has_usable_password():
            return None
        claimed_by = self.request.get_signed_cookie(
            NICKNAME_CLAIM_COOKIE,
            default=None,
            salt=NICKNAME_CLAIM_SALT,
            max_age=NICKNAME_CLAIM_MAX_AGE,
        )
        if claimed_by != str(user.pk):
            return None
        return user

    def get_success_url(self):
        next_url = self.request.GET.get("next")
        if next_url and url_has_allowed_host_and_scheme(
            next_url,
            allowed_hosts={self.request.get_host()},
            require_https=self.request.is_secure(),
        ):
            return next_url
        return "home"


class Home(
    LoginRequiredMixin,
    HTMXPartialMixin,
    View,
):
    template_name = "home.html"

    def get_context_data(self, *args, **kwargs):
        context = super().get_context_data(*args, **kwargs)
        context["parties"] = models.Party.objects.get_available_parties(
            self.request.user
        )
        return context

    def get(self, request, *args, **kwargs):
        context = self.get_context_data(**kwargs)
        return self.render_to_response(context)


class CreateParty(LoginRequiredMixin, HTMXPartialMixin, View):
    form_saved = False

    def get_template_names(self):
        if self.request.method == "POST" and self.form_saved is True:
            return ["home.html"]
        return ["create_party.html"]

    def get(self, request, *args, **kwargs):
        context = self.get_context_data(**kwargs)
        context["form"] = forms.PartyForm()
        return self.render_to_response(context)

    def post(self, request, *args, **kwargs):
        form = forms.PartyForm(request.POST)
        context = self.get_context_data(**kwargs)
        if not form.is_valid():
            context["form"] = form
            return self.render_to_response(
                context, headers={"HX-Reswap": "outerHTML transition:false"}
            )
        if request.POST.get("submit") != "true":
            context["form"] = form
            return self.render_to_response(
                context, headers={"HX-Reswap": "outerHTML transition:false"}
            )

        self.form_saved = True
        party, created = models.Party.objects.update_or_create(
            name=form.cleaned_data["name"], defaults=form.cleaned_data
        )
        if created:
            messages.add_message(
                request, messages.SUCCESS, f"'{party.name}' created successfully."
            )

        context["parties"] = models.Party.objects.get_available_parties(
            self.request.user
        )
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.send)(
            "party_state_machine",
            {
                "type": "party_stared",
                "party_name": party.name,
                "party_id": party.id,
            },
        )
        return self.render_to_response(
            context, headers={"HX-Reswap": "outerHTML transition:true"}
        )


class DetailParty(LoginRequiredMixin, HTMXPartialMixin, View):
    template_name = "party_no_started.html"

    def get_context_data(self, *args, **kwargs):
        context = super().get_context_data(*args, **kwargs)
        party_qs = models.Party.objects.filter(id=kwargs["party_id"]) & (
            models.Party.objects.filter(joined_users__pk=self.request.user.id)
            | models.Party.objects.filter(
                closed_at__isnull=True,
            )
        )
        party_qs = party_qs.order_by("pk").distinct("pk")
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


class PartyAnswers(LoginRequiredMixin, HTMXPartialMixin, View):
    template_name = "party_modal_answers.html"

    def get_context_data(self, *args, **kwargs):
        context = super().get_context_data(*args, **kwargs)
        party_qs = models.Party.objects.filter(id=kwargs["party_id"]) & (
            models.Party.objects.filter(joined_users__pk=self.request.user.id)
        )
        party_qs = party_qs.order_by("pk").distinct("pk")
        if not party_qs.exists():
            raise Http404()

        context["party"] = party_qs.get()
        user = User.objects.get(username=kwargs["username"])
        context["rounds"] = context["party"].get_answers_for_user(user)
        context["open"] = "open"
        return context

    def get(self, request, *args, **kwargs):
        context = self.get_context_data(**kwargs)
        return self.render_to_response(context)
