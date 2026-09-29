import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.models import User
from django.contrib.auth.views import LogoutView
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
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
NICKNAME_CLAIM_LIMIT = 10
LOGIN_REJECTED_MESSAGE = "No es posible iniciar sesión con ese nombre de usuario."
LOGIN_RATE_LIMITED_MESSAGE = "Demasiados intentos. Inténtalo de nuevo más tarde."
NICKNAME_CREATION_CACHE_KEY = "core.views.Login.nickname_creations:{ip}"


class NicknameCreationLimited(Exception):
    pass


def get_client_ip(request):
    """Return the client IP, trusting only the header set by our reverse proxy.

    A proxy appends the address it saw, so only the last entry can't be forged
    by the client.
    """
    if settings.CLIENT_IP_HEADER:
        forwarded = request.META.get(settings.CLIENT_IP_HEADER, "")
        client_ip = forwarded.rsplit(",", 1)[-1].strip()
        if client_ip:
            return client_ip
    return request.META["REMOTE_ADDR"]


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
        status = 200
        if form.is_valid():
            try:
                user = self.get_nickname_user(form.cleaned_data["nickname"])
            except NicknameCreationLimited:
                form.add_error(None, LOGIN_RATE_LIMITED_MESSAGE)
                status = 429
            else:
                if user is not None:
                    return self.login_and_redirect(user)
                form.add_error(None, LOGIN_REJECTED_MESSAGE)

        context = self.get_context_data(**kwargs)
        context["form"] = form
        return self.render_to_response(context, status=status)

    def login_and_redirect(self, user):
        login(self.request, user)
        response = redirect(self.get_success_url())
        claimed = [user.pk] + [pk for pk in self.get_claimed_pks() if pk != user.pk]
        response.set_signed_cookie(
            NICKNAME_CLAIM_COOKIE,
            ",".join(map(str, claimed[:NICKNAME_CLAIM_LIMIT])),
            salt=NICKNAME_CLAIM_SALT,
            max_age=NICKNAME_CLAIM_MAX_AGE,
            secure=self.request.is_secure(),
            httponly=True,
            samesite="Lax",
        )
        return response

    def get_nickname_user(self, nickname):
        """Return the user for this nickname, or None if this browser may not use it.

        A nickname belongs to the browser that first claimed it, proven by a
        signed cookie. Staff, superusers and accounts with a password must use
        /admin/login/ instead.
        """
        user = User.objects.filter(username__iexact=nickname).order_by("pk").first()
        if user is None:
            self.count_nickname_creation()
            user = User(username=nickname)
            user.set_unusable_password()
            try:
                with transaction.atomic():
                    user.save()
            except IntegrityError:
                return None
            return user

        if (
            user.is_staff
            or user.is_superuser
            or user.has_usable_password()
            or not user.is_active
        ):
            return None
        if user.pk not in self.get_claimed_pks():
            return None
        return user

    def count_nickname_creation(self):
        key = NICKNAME_CREATION_CACHE_KEY.format(ip=get_client_ip(self.request))
        window = settings.LOGIN_NICKNAME_CREATION_WINDOW
        cache.add(key, 0, timeout=window)
        try:
            count = cache.incr(key)
        except ValueError:
            cache.set(key, 1, timeout=window)
            count = 1
        if count > settings.LOGIN_NICKNAME_CREATION_LIMIT:
            raise NicknameCreationLimited

    def get_claimed_pks(self):
        value = self.request.get_signed_cookie(
            NICKNAME_CLAIM_COOKIE,
            default="",
            salt=NICKNAME_CLAIM_SALT,
            max_age=NICKNAME_CLAIM_MAX_AGE,
        )
        return [int(pk) for pk in value.split(",") if pk.isdigit()]

    def get_success_url(self):
        next_url = self.request.GET.get("next")
        if next_url and url_has_allowed_host_and_scheme(
            next_url,
            allowed_hosts={self.request.get_host()},
            require_https=self.request.is_secure(),
        ):
            return next_url
        return "home"


class Logout(LogoutView):
    http_method_names = ["post", "options"]


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

        party = form.save(commit=False)
        party.created_by = request.user
        try:
            with transaction.atomic():
                party.save()
        except IntegrityError:
            form.add_error("name", models.DUPLICATE_OPEN_PARTY_NAME_MESSAGE)
            context["form"] = form
            return self.render_to_response(
                context, headers={"HX-Reswap": "outerHTML transition:false"}
            )

        self.form_saved = True
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
        context["players_scores"] = self.party.get_players_scores()
        context["rounds"] = self.party.get_answers_for_user(self.request.user)
        current_round = context["current_round"] = self.party.get_current_round()
        if self.party.closed_at:
            context["winners"] = self.party.get_winners()
            return context
        context["form"] = None
        if current_round is None:
            return context
        disabled = current_round.closed_at is not None
        context["disabled"] = disabled
        context["form"] = forms.CurrentAnswersForm(
            current_round=current_round,
            disabled=disabled,
            initial=async_to_sync(current_round.aget_initial_data_for_user)(
                self.request.user
            ),
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

        party = context["party"] = party_qs.get()
        user = get_object_or_404(User, username=kwargs["username"])
        is_participant = (
            party.joined_users.filter(pk=user.pk).exists()
            or models.UserRoundAnswer.objects.filter(
                user=user, round__party=party
            ).exists()
        )
        if not is_participant:
            raise Http404()
        context["rounds"] = party.get_answers_for_user(
            user, closed_rounds_only=user != self.request.user
        )
        context["open"] = "open"
        return context

    def get(self, request, *args, **kwargs):
        context = self.get_context_data(**kwargs)
        return self.render_to_response(context)
