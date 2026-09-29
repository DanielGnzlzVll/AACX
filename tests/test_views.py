import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.models import Party, PartyRound, UserRoundAnswer


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


@pytest.mark.parametrize(
    "data",
    [
        pytest.param({"name": "new party", "max_rounds": 4}, id="validation-only"),
        pytest.param(
            {"name": "new party", "max_rounds": 99, "submit": "true"},
            id="invalid-form",
        ),
    ],
)
def test_create_party_does_not_create_party(logged_in_client, data):
    response = logged_in_client.post(reverse("create_party"), data)

    assert response.status_code == 200
    assert not Party.objects.exists()
    assert response.headers["HX-Reswap"] == "outerHTML transition:false"


@pytest.mark.parametrize(
    "state, joined, expected_status, expected_template",
    [
        ("not-started", False, 200, "party_no_started.html"),
        ("started", False, 200, "party.html"),
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
