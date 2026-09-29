import pytest
from asgiref.sync import async_to_sync

from core.models import PartyRound, UserRoundAnswer


@pytest.mark.parametrize(
    "answers, expected_points",
    [
        pytest.param({"alice": "Ana"}, {"alice": 100}, id="unique"),
        pytest.param({"alice": "ana"}, {"alice": 100}, id="letter-case-insensitive"),
        pytest.param(
            {"alice": "Ana", "bob": "Andres"},
            {"alice": 100, "bob": 100},
            id="different-answers",
        ),
        pytest.param(
            {"alice": "Ana", "bob": "Ana"}, {"alice": 50, "bob": 50}, id="shared-by-2"
        ),
        pytest.param(
            {"alice": "Ana", "bob": "Ana", "carol": "Ana"},
            {"alice": 33, "bob": 33, "carol": 33},
            id="shared-by-3",
        ),
        pytest.param(
            {"alice": "Ana", "bob": "Ana", "carol": "Alba"},
            {"alice": 50, "bob": 50, "carol": 100},
            id="shared-and-unique",
        ),
        pytest.param({"alice": "Pedro"}, {"alice": 0}, id="wrong-letter"),
        pytest.param({"alice": ""}, {"alice": 0}, id="empty"),
        pytest.param(
            {"alice": "", "bob": "", "carol": "Ana"},
            {"alice": 0, "bob": 0, "carol": 100},
            id="empty-answers-do-not-share-points",
        ),
    ],
)
def test_close_round_and_calculate_scores(
    answers, expected_points, party_factory, user_factory
):
    round = PartyRound.objects.create(party=party_factory(), letter="A")
    for username, value in answers.items():
        UserRoundAnswer.objects.create(
            round=round, user=user_factory(username), field="name", value=value
        )

    async_to_sync(round.close_round_and_calculate_scores)()

    round.refresh_from_db()
    assert round.closed_at is not None
    scored = {
        answer.user.username: answer.scored_points or 0
        for answer in UserRoundAnswer.objects.filter(round=round)
    }
    assert scored == expected_points


def test_scores_are_calculated_per_field(party_factory, alice, bob):
    round = PartyRound.objects.create(party=party_factory(), letter="C")
    for user, field, value in [
        (alice, "country", "Colombia"),
        (bob, "country", "Colombia"),
        (alice, "city", "Cali"),
        (bob, "city", "Cartagena"),
        (alice, "color", "Colombia"),
    ]:
        UserRoundAnswer.objects.create(round=round, user=user, field=field, value=value)

    async_to_sync(round.close_round_and_calculate_scores)()

    scored = {
        (answer.user.username, answer.field): answer.scored_points
        for answer in UserRoundAnswer.objects.filter(round=round)
    }
    assert scored == {
        ("alice", "country"): 50,
        ("bob", "country"): 50,
        ("alice", "city"): 100,
        ("bob", "city"): 100,
        ("alice", "color"): 100,
    }
