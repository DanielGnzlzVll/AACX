from html.parser import HTMLParser
from unittest import mock

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.forms import PartyForm
from core.models import (
    DUPLICATE_OPEN_PARTY_NAME_MESSAGE,
    Party,
    PartyRound,
    UserRoundAnswer,
)

PARTY_SETTINGS = {"min_players": 2, "max_round_duration": 120}


@pytest.mark.parametrize(
    "url",
    [
        reverse("home"),
        reverse("create_party"),
        reverse("detail_party", kwargs={"party_id": 1}),
        reverse("party_answers", kwargs={"party_id": 1, "username": "alice"}),
    ],
)
def test_views_require_login(url, db):
    response = Client().get(url)

    assert response.status_code == 302
    assert response.url == f"{reverse('login')}?next={url}"


def test_home_lists_available_parties(logged_in_client, party_factory, alice):
    open_party = party_factory(name="open")
    joined_party = party_factory(
        name="joined", started_at=timezone.now(), joined_users=[alice]
    )
    party_factory(name="started", started_at=timezone.now())
    party_factory(
        name="finished",
        started_at=timezone.now(),
        closed_at=timezone.now(),
        joined_users=[alice],
    )

    response = logged_in_client.get(reverse("home"))

    assert response.status_code == 200
    assert set(response.context["parties"]) == {open_party, joined_party}


def test_create_party_renders_form(logged_in_client):
    response = logged_in_client.get(reverse("create_party"))

    assert response.status_code == 200
    assert "form" in response.context


def test_create_party_creates_party(logged_in_client):
    response = logged_in_client.post(
        reverse("create_party"),
        {
            "name": "new party",
            "min_players": 3,
            "max_round_duration": 60,
            "max_rounds": 4,
            "submit": "true",
        },
    )

    assert response.status_code == 200
    party = Party.objects.get(name="new party")
    assert (party.min_players, party.max_round_duration, party.max_rounds) == (3, 60, 4)
    assert party in response.context["parties"]
    assert "Partida &#x27;new party&#x27; creada." in response.content.decode()


def test_create_party_stores_its_creator(logged_in_client, alice):
    logged_in_client.post(
        reverse("create_party"),
        {"name": "new party", "max_rounds": 4, "submit": "true"} | PARTY_SETTINGS,
    )

    assert Party.objects.get(name="new party").created_by == alice


@pytest.mark.parametrize("name", ["hijack", "HiJack"])
def test_create_party_with_an_open_party_name_is_rejected(
    logged_in_client, party_factory, bob, name
):
    existing = party_factory(
        name="hijack",
        min_players=2,
        max_round_duration=120,
        max_rounds=5,
        created_by=bob,
    )

    response = logged_in_client.post(
        reverse("create_party"),
        {
            "name": name,
            "min_players": 9,
            "max_round_duration": 30,
            "max_rounds": 1,
            "submit": "true",
        },
    )

    assert response.status_code == 200
    assert response.context["form"].errors == {
        "name": [DUPLICATE_OPEN_PARTY_NAME_MESSAGE]
    }
    assert response.headers["HX-Reswap"] == "outerHTML transition:false"
    assert list(Party.objects.all()) == [existing]
    existing.refresh_from_db()
    assert (
        existing.min_players,
        existing.max_round_duration,
        existing.max_rounds,
        existing.created_by,
    ) == (2, 120, 5, bob)


def test_create_party_reuses_the_name_of_a_closed_party(
    logged_in_client, party_factory, alice
):
    closed = party_factory(
        name="hijack", max_rounds=5, started_at=timezone.now(), closed_at=timezone.now()
    )

    logged_in_client.post(
        reverse("create_party"),
        {"name": "hijack", "max_rounds": 1, "submit": "true"} | PARTY_SETTINGS,
    )

    closed.refresh_from_db()
    assert closed.max_rounds == 5
    new_party = Party.objects.exclude(pk=closed.pk).get(name="hijack")
    assert (new_party.max_rounds, new_party.created_by) == (1, alice)


def test_create_party_losing_a_race_for_the_name_is_rejected(
    logged_in_client, party_factory
):
    existing = party_factory(name="hijack")

    with mock.patch.object(
        PartyForm, "clean_name", lambda form: form.cleaned_data["name"]
    ):
        response = logged_in_client.post(
            reverse("create_party"),
            {"name": "hijack", "max_rounds": 1, "submit": "true"} | PARTY_SETTINGS,
        )

    assert response.status_code == 200
    assert response.context["form"].errors == {
        "name": [DUPLICATE_OPEN_PARTY_NAME_MESSAGE]
    }
    assert list(Party.objects.all()) == [existing]


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(
            {"name": "new party", "max_rounds": 4} | PARTY_SETTINGS,
            id="validation-only",
        ),
        pytest.param(
            {"name": "new party", "max_rounds": 99, "submit": "true"} | PARTY_SETTINGS,
            id="invalid-form",
        ),
        pytest.param(
            {
                "name": "new party",
                "min_players": "",
                "max_round_duration": "",
                "max_rounds": "",
                "submit": "true",
            },
            id="blank-settings",
        ),
    ],
)
def test_create_party_does_not_create_party(logged_in_client, data):
    response = logged_in_client.post(reverse("create_party"), data)

    assert response.status_code == 200
    assert not Party.objects.exists()
    assert response.headers["HX-Reswap"] == "outerHTML transition:false"


def test_create_party_with_blank_settings_shows_errors(logged_in_client):
    response = logged_in_client.post(
        reverse("create_party"),
        {"name": "new party", "min_players": "", "max_rounds": 0, "submit": "true"},
    )

    assert response.context["form"].errors == {
        "min_players": ["Este campo es obligatorio."],
        "max_round_duration": ["Este campo es obligatorio."],
        "max_rounds": ["El valor debe ser mayor o igual a 1."],
    }
    assert "Este campo es obligatorio." in response.content.decode()
    assert not Party.objects.exists()


@pytest.mark.parametrize(
    "state, joined, expected_status, expected_template",
    [
        ("not-started", False, 200, "party_no_started.html"),
        ("not-started", True, 200, "party_no_started.html"),
        ("started", False, 200, "party_started.html"),
        ("started", True, 200, "party.html"),
        ("closed", True, 200, "party.html"),
        ("closed", False, 404, None),
    ],
)
def test_detail_party(
    logged_in_client,
    party_factory,
    alice,
    state,
    joined,
    expected_status,
    expected_template,
):
    fields = {
        "not-started": {},
        "started": {"started_at": timezone.now()},
        "closed": {"started_at": timezone.now(), "closed_at": timezone.now()},
    }[state]
    party = party_factory(joined_users=[alice] if joined else [], **fields)

    response = logged_in_client.get(
        reverse("detail_party", kwargs={"party_id": party.id})
    )

    assert response.status_code == expected_status
    if expected_template:
        assert expected_template in [t.name for t in response.templates]


def create_round(party, letter, closed):
    return PartyRound.objects.create(
        party=party, letter=letter, closed_at=timezone.now() if closed else None
    )


@pytest.mark.parametrize(
    "state, rounds",
    [
        ("not-started", []),
        ("started", []),
        ("started", [False]),
        ("started", [True]),
        ("started", [True, False]),
        ("closed", [True, True]),
    ],
)
def test_detail_party_get_does_not_write(
    logged_in_client, party_factory, alice, state, rounds
):
    fields = {
        "not-started": {},
        "started": {"started_at": timezone.now()},
        "closed": {"started_at": timezone.now(), "closed_at": timezone.now()},
    }[state]
    party = party_factory(joined_users=[alice], **fields)
    for letter, closed in zip("AB", rounds):
        create_round(party, letter, closed)

    with CaptureQueriesContext(connection) as queries:
        response = logged_in_client.get(
            reverse("detail_party", kwargs={"party_id": party.id})
        )

    assert response.status_code == 200
    assert PartyRound.objects.filter(party=party).count() == len(rounds)
    writes = [
        query["sql"]
        for query in queries
        if query["sql"].lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE"}
    ]
    assert writes == []


def get_detail_party(client, party):
    return client.get(reverse("detail_party", kwargs={"party_id": party.id}))


def test_detail_party_waits_for_first_round(logged_in_client, party_factory, alice):
    party = party_factory(started_at=timezone.now(), joined_users=[alice])

    response = get_detail_party(logged_in_client, party)

    assert response.context["current_round"] is None
    assert response.context["form"] is None
    assert "ws-send" not in response.content.decode()


def test_detail_party_open_round_is_editable(logged_in_client, party_factory, alice):
    party = party_factory(started_at=timezone.now(), joined_users=[alice])
    create_round(party, "A", closed=True)
    open_round = create_round(party, "B", closed=False)

    response = get_detail_party(logged_in_client, party)

    assert response.context["current_round"] == open_round
    assert response.context["form"].current_round == open_round
    assert not response.context["form"].disabled
    assert "ws-send" in response.content.decode()


class AnswersFormTags(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.form = None
        self.tags = []
        self.depth = 0
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if self.form is None and attrs.get("id") == "party_current_answers_form":
            self.form = attrs
            self.depth = 1
            return
        if self.depth:
            self.tags.append((tag, attrs))
            if tag == "form":
                self.depth += 1

    def handle_endtag(self, tag):
        if self.depth and tag == "form":
            self.depth -= 1


def test_detail_party_open_round_shows_saved_answers(
    logged_in_client, party_factory, alice
):
    party = party_factory(started_at=timezone.now(), joined_users=[alice])
    open_round = create_round(party, "A", closed=False)
    UserRoundAnswer.objects.create(
        round=open_round, user=alice, field="name", value="Ana"
    )

    response = get_detail_party(logged_in_client, party)

    assert response.context["form"].initial == {"name": "Ana"}
    inputs = {
        attrs["name"]: attrs.get("value")
        for tag, attrs in AnswersFormTags(response.content.decode()).tags
        if tag == "input"
    }
    assert inputs["name"] == "Ana"


def test_answers_form_sends_every_change_and_enter_cannot_stop(
    logged_in_client, party_factory, alice
):
    party = party_factory(started_at=timezone.now(), joined_users=[alice])
    create_round(party, "A", closed=False)

    parsed = AnswersFormTags(get_detail_party(logged_in_client, party).content.decode())

    assert parsed.form["hx-trigger"] == "input delay:200ms"
    tags = [tag for tag, _ in parsed.tags]
    assert "script" not in tags
    buttons = [attrs for tag, attrs in parsed.tags if tag == "button"]
    assert [button["id"] for button in buttons] == ["submit_stop"]
    assert all(button.get("type") == "button" for button in buttons)
    assert not any(
        attrs.get("type") in ("submit", "image")
        for tag, attrs in parsed.tags
        if tag == "input"
    )


def test_detail_party_between_rounds_shows_closed_round_disabled(
    logged_in_client, party_factory, alice
):
    party = party_factory(started_at=timezone.now(), joined_users=[alice])
    closed_round = create_round(party, "A", closed=True)
    UserRoundAnswer.objects.create(
        round=closed_round, user=alice, field="name", value="Ana"
    )

    response = get_detail_party(logged_in_client, party)

    assert response.context["current_round"] == closed_round
    assert response.context["form"].disabled
    assert response.context["form"].initial == {"name": "Ana"}
    assert "ws-send" not in response.content.decode()


def test_detail_party_started_without_user_is_read_only(
    logged_in_client, party_factory, bob
):
    party = party_factory(started_at=timezone.now(), joined_users=[bob])
    open_round = create_round(party, "A", closed=False)
    UserRoundAnswer.objects.create(
        round=open_round, user=bob, field="name", value="Ana", scored_points=100
    )

    response = get_detail_party(logged_in_client, party)

    content = response.content.decode()
    assert "Esta partida ya empezó" in content
    assert "ws-connect" not in content
    assert "<form" not in content.split('id="content"')[1]
    assert "form" not in response.context
    assert response.context["players_scores"] == {"bob": 100}
    assert "bob" in content
    assert f'href="{reverse("home")}"' in content


def test_detail_party_not_found(logged_in_client):
    response = logged_in_client.get(reverse("detail_party", kwargs={"party_id": 999}))

    assert response.status_code == 404


@pytest.mark.parametrize("joined, expected_status", [(True, 200), (False, 404)])
def test_party_answers(
    logged_in_client, party_factory, alice, bob, joined, expected_status
):
    party = party_factory(joined_users=[alice, bob] if joined else [bob])

    response = logged_in_client.get(
        reverse("party_answers", kwargs={"party_id": party.id, "username": "bob"})
    )

    assert response.status_code == expected_status


@pytest.fixture
def party_with_rounds(party_factory, alice, bob):
    party = party_factory(started_at=timezone.now(), joined_users=[alice, bob])
    closed_round = PartyRound.objects.create(
        party=party, letter="A", closed_at=timezone.now()
    )
    open_round = PartyRound.objects.create(party=party, letter="B")
    for user in (alice, bob):
        UserRoundAnswer.objects.create(
            round=closed_round, user=user, field="name", value=f"A-{user.username}"
        )
        UserRoundAnswer.objects.create(
            round=open_round, user=user, field="name", value=f"B-{user.username}"
        )
    return party


def get_party_answers(client, party, username):
    return client.get(
        reverse("party_answers", kwargs={"party_id": party.id, "username": username})
    )


def test_party_answers_shows_own_open_round(logged_in_client, party_with_rounds):
    response = get_party_answers(logged_in_client, party_with_rounds, "alice")

    assert response.status_code == 200
    assert response.context["rounds"] == [
        {"letter": "A", "name": "A-alice"},
        {"letter": "B", "name": "B-alice"},
    ]


def test_party_answers_hides_other_players_open_round(
    logged_in_client, party_with_rounds
):
    response = get_party_answers(logged_in_client, party_with_rounds, "bob")

    assert response.status_code == 200
    assert response.context["rounds"] == [{"letter": "A", "name": "A-bob"}]
    assert b"B-bob" not in response.content


def test_party_answers_for_player_who_left_the_party(
    logged_in_client, party_with_rounds, bob
):
    party_with_rounds.joined_users.remove(bob)

    response = get_party_answers(logged_in_client, party_with_rounds, "bob")

    assert response.status_code == 200
    assert response.context["rounds"] == [{"letter": "A", "name": "A-bob"}]


def test_party_answers_unknown_user(logged_in_client, party_with_rounds):
    response = get_party_answers(logged_in_client, party_with_rounds, "nobody")

    assert response.status_code == 404


def test_party_answers_non_participant(
    logged_in_client, party_with_rounds, user_factory
):
    user_factory("carol")

    response = get_party_answers(logged_in_client, party_with_rounds, "carol")

    assert response.status_code == 404
