import datetime

import pytest
from django.db import IntegrityError
from django.utils import timezone

from core import models

pytestmark = pytest.mark.django_db(transaction=True)

Reason = models.PartyRound.ClosedReason


@pytest.fixture
def party(party_factory, alice):
    return party_factory(max_rounds=3, started_at=timezone.now(), joined_users=[alice])


async def play_rounds(party, count):
    rounds = []
    for _ in range(count):
        current = await party.aget_current_or_next_round()
        await current.close_round_and_calculate_scores()
        rounds.append(current)
    return rounds


async def test_rounds_are_numbered_in_the_order_they_are_played(party):
    rounds = await play_rounds(party, 3)

    assert [round.number for round in rounds] == [1, 2, 3]


async def test_rounds_of_different_parties_are_numbered_independently(
    party, party_factory
):
    other = await models.Party.objects.acreate(name="other", started_at=timezone.now())
    await play_rounds(party, 2)

    assert (await other.aget_current_or_next_round()).number == 1


async def test_current_round_is_the_one_with_the_highest_number(party):
    first = await models.PartyRound.objects.acreate(party=party, letter="A")
    later_start = first.started_at - datetime.timedelta(minutes=1)
    second = await models.PartyRound.objects.acreate(
        party=party, letter="B", started_at=later_start
    )

    assert second.number == 2
    assert (await party.aget_current_round()).pk == second.pk


def test_a_round_number_is_unique_per_party(party):
    models.PartyRound.objects.create(party=party, letter="A", number=1)

    with pytest.raises(IntegrityError):
        models.PartyRound.objects.create(party=party, letter="B", number=1)


async def test_past_answers_are_listed_in_round_order(party, alice):
    first = await models.PartyRound.objects.acreate(party=party, letter="Z", number=1)
    second = await models.PartyRound.objects.acreate(party=party, letter="A", number=2)
    for round in (second, first):
        await models.UserRoundAnswer.objects.acreate(
            round=round, user=alice, field="name", value=f"{round.letter}na"
        )

    answers = await party.aget_answers_for_user(alice)

    assert [answer["letter"] for answer in answers] == ["Z", "A"]


async def test_closing_records_the_reason(party):
    party_round = await party.aget_current_or_next_round()

    assert await party_round.close(Reason.STOP)

    await party_round.arefresh_from_db()
    assert party_round.closed_reason == Reason.STOP


async def test_a_late_close_keeps_the_first_reason(party):
    party_round = await party.aget_current_or_next_round()
    await party_round.close(Reason.STOP)

    assert not await party_round.close(Reason.TIMEOUT)

    await party_round.arefresh_from_db()
    assert party_round.closed_reason == Reason.STOP


async def test_scoring_an_open_round_closes_it_as_timed_out(party):
    party_round = await party.aget_current_or_next_round()

    await party_round.close_round_and_calculate_scores()

    await party_round.arefresh_from_db()
    assert party_round.closed_reason == Reason.TIMEOUT


async def test_scoring_keeps_the_reason_of_a_stopped_round(party):
    party_round = await party.aget_current_or_next_round()
    await party_round.close(Reason.STOP)

    await party_round.close_round_and_calculate_scores()

    await party_round.arefresh_from_db()
    assert party_round.closed_reason == Reason.STOP
