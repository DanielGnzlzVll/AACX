import asyncio
from unittest import mock

import msgpack
from asgiref.sync import async_to_sync
from channels.layers import InMemoryChannelLayer, get_channel_layer
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.contrib.auth import SESSION_KEY, get_user_model
from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core import consumers, models, routing
from core.views import LOGIN_REJECTED_MESSAGE, NICKNAME_CLAIM_COOKIE

SIMPLE_STORAGES = {
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}


@override_settings(STORAGES=SIMPLE_STORAGES)
class LoginTests(TestCase):
    def post_login(self, data, url=None):
        return self.client.post(url or reverse("login"), data)

    def assert_logged_out(self):
        self.assertNotIn(SESSION_KEY, self.client.session)

    def assert_rejected(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "login.html")
        self.assertContains(response, LOGIN_REJECTED_MESSAGE)
        self.assert_logged_out()

    def test_get_renders_form(self):
        response = self.client.get(reverse("login"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="nickname"')

    def test_new_nickname_creates_passwordless_user_and_logs_in(self):
        response = self.post_login({"nickname": "  ana  "})

        self.assertRedirects(response, reverse("home"))
        user = User.objects.get(username="ana")
        self.assertFalse(user.has_usable_password())
        self.assertEqual(int(self.client.session[SESSION_KEY]), user.pk)
        self.assertIn(NICKNAME_CLAIM_COOKIE, response.cookies)

    def test_superuser_username_is_rejected(self):
        User.objects.create_superuser("root", "root@example.com", "s3cret-pass")

        response = self.post_login({"nickname": "root"})

        self.assert_rejected(response)
        self.assertRedirects(
            self.client.get("/admin/"),
            "/admin/login/?next=/admin/",
            fetch_redirect_response=False,
        )

    def test_superuser_username_with_different_case_is_rejected(self):
        User.objects.create_superuser("root", "root@example.com", "s3cret-pass")

        response = self.post_login({"nickname": "ROOT"})

        self.assert_rejected(response)
        self.assertFalse(User.objects.filter(username="ROOT").exists())

    def test_staff_username_is_rejected(self):
        user = User(username="staffer", is_staff=True)
        user.set_unusable_password()
        user.save()

        self.assert_rejected(self.post_login({"nickname": "staffer"}))

    def test_user_with_password_is_rejected(self):
        User.objects.create_user("carla", password="s3cret-pass")

        self.assert_rejected(self.post_login({"nickname": "carla"}))

    def test_nickname_claimed_by_another_browser_is_rejected(self):
        Client().post(reverse("login"), {"nickname": "ana"})

        self.assert_rejected(self.post_login({"nickname": "ana"}))

    def test_nickname_with_tampered_claim_cookie_is_rejected(self):
        self.post_login({"nickname": "ana"})
        self.client.post(reverse("logout"))
        other = User(username="bob")
        other.set_unusable_password()
        other.save()
        self.client.cookies[NICKNAME_CLAIM_COOKIE] = str(other.pk)

        self.assert_rejected(self.post_login({"nickname": "bob"}))

    def test_same_browser_can_log_back_in_with_its_nickname(self):
        self.post_login({"nickname": "ana"})
        self.client.post(reverse("logout"))

        response = self.post_login({"nickname": "ana"})

        self.assertRedirects(response, reverse("home"))
        self.assertEqual(User.objects.filter(username="ana").count(), 1)
        self.assertEqual(
            int(self.client.session[SESSION_KEY]),
            User.objects.get(username="ana").pk,
        )

    def test_invalid_nicknames_rerender_form_with_errors(self):
        cases = {
            "missing": {},
            "empty": {"nickname": ""},
            "blank": {"nickname": "   "},
            "too short": {"nickname": "ab"},
            "too long": {"nickname": "a" * 31},
            "invalid characters": {"nickname": "<script>"},
            "spaces inside": {"nickname": "ana maria"},
        }
        for case, data in cases.items():
            with self.subTest(case):
                response = self.post_login(data)

                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, "login.html")
                self.assertTrue(response.context["form"].errors)
                self.assert_logged_out()

        self.assertFalse(User.objects.exists())

    def test_redirects_to_safe_next_url(self):
        response = self.post_login(
            {"nickname": "ana"}, url=f"{reverse('login')}?next=/party/create/"
        )

        self.assertRedirects(response, "/party/create/", fetch_redirect_response=False)

    def test_ignores_external_next_url(self):
        response = self.post_login(
            {"nickname": "ana"}, url=f"{reverse('login')}?next=https://evil.example/"
        )

        self.assertRedirects(response, reverse("home"))


@override_settings(STORAGES=SIMPLE_STORAGES)
class LogoutTests(TestCase):
    def setUp(self):
        self.client.post(reverse("login"), {"nickname": "ana"})

    def test_navbar_links_to_logout(self):
        response = self.client.get(reverse("home"))

        self.assertContains(response, f'action="{reverse("logout")}"')

    def test_logout_ends_session_and_redirects_to_login(self):
        response = self.client.post(reverse("logout"))

        self.assertRedirects(response, reverse("login"))
        self.assertNotIn(SESSION_KEY, self.client.session)


class MsgpackInMemoryChannelLayer(InMemoryChannelLayer):
    """Round-trips every message through msgpack, as channels_redis does."""

    @staticmethod
    def _roundtrip(message):
        return msgpack.unpackb(msgpack.packb(message, use_bin_type=True), raw=False)

    async def send(self, channel, message):
        await super().send(channel, self._roundtrip(message))

    async def group_send(self, group, message):
        await super().group_send(group, self._roundtrip(message))


MSGPACK_CHANNEL_LAYERS = {
    "default": {"BACKEND": "core.tests.MsgpackInMemoryChannelLayer"}
}


async def receive_or_none(layer, channel, timeout=0.2):
    try:
        return await asyncio.wait_for(layer.receive(channel), timeout=timeout)
    except TimeoutError:
        return None


@override_settings(CHANNEL_LAYERS=MSGPACK_CHANNEL_LAYERS)
class StopRoundTests(TestCase):
    def setUp(self):
        self.layer = get_channel_layer()
        self.user = get_user_model().objects.create(username="player1")
        self.party = models.Party.objects.create(
            name="party", started_at=timezone.now()
        )
        self.round = models.PartyRound.objects.create(
            party=self.party, letter="A", started_at=timezone.now()
        )
        self.new_round_channel = f"party_new_round_{self.party.id}"
        async_to_sync(self.layer.group_add)(f"party_{self.party.id}", "probe")
        self.state_machine = consumers.PartyStateMachine()
        self.state_machine.channel_layer = self.layer

    def tearDown(self):
        async_to_sync(self.layer.flush)()

    def stop_event(self):
        return {
            "type": "event_party_round_stopped",
            "party_id": self.party.id,
            "round_id": self.round.id,
        }

    async def test_stop_button_sends_only_primitives_to_state_machine(self):
        communicator = WebsocketCommunicator(
            URLRouter(routing.websocket_urlpatterns), f"/party/{self.party.id}/"
        )
        communicator.scope["user"] = self.user
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        self.assertEqual(
            await communicator.receive_from(), "waiting for players to join"
        )
        started = await receive_or_none(
            self.layer, consumers.STATE_MACHINE_CHANNEL_NAME
        )
        self.assertEqual(started["type"], "event_party_started")

        await communicator.send_json_to(
            {
                "HEADERS": {"HX-Trigger": "party_current_answers_form"},
                "name": "Ana",
                "submit_stop": "on",
            }
        )

        stopped = await receive_or_none(
            self.layer, consumers.STATE_MACHINE_CHANNEL_NAME, timeout=2
        )
        self.assertEqual(stopped, self.stop_event())
        self.assertTrue(await communicator.receive_nothing())
        await communicator.disconnect()

    async def test_stop_closes_round_and_notifies_players(self):
        await self.state_machine.event_party_round_stopped(self.stop_event())

        await self.round.arefresh_from_db()
        self.assertIsNotNone(self.round.closed_at)
        notification = await receive_or_none(self.layer, "probe")
        self.assertEqual(notification["type"], "event_party_round_stopped")
        round_end = await receive_or_none(self.layer, self.new_round_channel)
        self.assertEqual(round_end, {"round_id": self.round.id})

    async def test_close_updates_the_instance_only_once(self):
        self.assertTrue(await self.round.close())
        self.assertIsNotNone(self.round.closed_at)
        self.assertFalse(await self.round.close())

    async def test_duplicate_stops_end_the_round_once(self):
        await asyncio.gather(
            self.state_machine.event_party_round_stopped(self.stop_event()),
            self.state_machine.event_party_round_stopped(self.stop_event()),
        )

        self.assertIsNotNone(await receive_or_none(self.layer, "probe"))
        self.assertIsNone(await receive_or_none(self.layer, "probe"))
        self.assertIsNotNone(await receive_or_none(self.layer, self.new_round_channel))
        self.assertIsNone(await receive_or_none(self.layer, self.new_round_channel))

    async def test_stop_for_closed_round_is_ignored(self):
        self.round.closed_at = timezone.now()
        await self.round.asave()

        await self.state_machine.event_party_round_stopped(self.stop_event())

        self.assertIsNone(await receive_or_none(self.layer, "probe"))
        self.assertIsNone(await receive_or_none(self.layer, self.new_round_channel))

    async def test_stale_round_end_does_not_end_the_current_round(self):
        self.party.max_round_duration = 60
        await self.layer.send(self.new_round_channel, {"round_id": self.round.id - 1})

        with self.assertRaises(TimeoutError):
            await asyncio.wait_for(
                self.state_machine.wait_for_round_end(self.party, self.round),
                timeout=0.3,
            )

        await self.layer.send(self.new_round_channel, {"round_id": self.round.id})
        await asyncio.wait_for(
            self.state_machine.wait_for_round_end(self.party, self.round), timeout=1
        )

    async def test_round_timeout_closes_round_and_notifies_players(self):
        self.party.max_round_duration = 0

        await self.state_machine.wait_for_round_end(self.party, self.round)

        await self.round.arefresh_from_db()
        self.assertIsNotNone(self.round.closed_at)
        notification = await receive_or_none(self.layer, "probe")
        self.assertEqual(notification["type"], "event_party_round_stopped")

    @mock.patch.object(consumers.PartyStateMachine, "display_all_answers")
    async def test_scores_the_stopped_round_without_creating_a_new_one(self, _):
        await models.UserRoundAnswer.objects.acreate(
            round=self.round, user=self.user, field="name", value="Ana"
        )
        await self.state_machine.event_party_round_stopped(self.stop_event())

        await self.state_machine.update_scores(self.party, self.round)

        self.assertEqual(
            await models.PartyRound.objects.filter(party=self.party).acount(), 1
        )
        answer = await models.UserRoundAnswer.objects.aget(round=self.round)
        self.assertEqual(answer.scored_points, 100)
