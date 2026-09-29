from django.contrib.auth import SESSION_KEY
from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse

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
