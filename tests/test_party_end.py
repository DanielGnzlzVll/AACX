import pytest
from django.urls import reverse
from django.utils import timezone
from pytest_django.asserts import assertContains, assertNotContains

from core import models

pytestmark = pytest.mark.django_db


def test_open_party_is_active(party_factory):
    assert party_factory().is_active


def test_closed_party_is_not_active(party_factory):
    assert not party_factory(closed_at=timezone.now()).is_active


@pytest.fixture
def two_round_party(party_factory):
    return party_factory(max_rounds=2, started_at=timezone.now())


@pytest.mark.django_db(transaction=True)
async def test_closing_a_round_before_the_last_keeps_the_party_open(two_round_party):
    party_round = await models.PartyRound.objects.acreate(
        party=two_round_party, letter="A"
    )
    await party_round.close_round_and_calculate_scores()

    await two_round_party.arefresh_from_db()
    assert party_round.closed_at is not None
    assert two_round_party.closed_at is None


@pytest.mark.django_db(transaction=True)
async def test_closing_the_last_round_closes_the_party(two_round_party):
    first = await models.PartyRound.objects.acreate(party=two_round_party, letter="A")
    await first.close_round_and_calculate_scores()
    last = await models.PartyRound.objects.acreate(party=two_round_party, letter="B")
    await last.close_round_and_calculate_scores()

    await two_round_party.arefresh_from_db()
    assert two_round_party.closed_at is not None


@pytest.mark.django_db(transaction=True)
async def test_closing_keeps_an_existing_closed_at(two_round_party):
    closed_at = timezone.now() - timezone.timedelta(seconds=5)
    party_round = await models.PartyRound.objects.acreate(
        party=two_round_party, letter="A", closed_at=closed_at
    )

    await party_round.close_round_and_calculate_scores()

    await party_round.arefresh_from_db()
    assert party_round.closed_at == closed_at


def test_winners_are_the_players_with_the_highest_score(
    party_factory, user_factory, alice, bob
):
    carol = user_factory("carol")
    party_round = models.PartyRound.objects.create(party=party_factory(), letter="A")
    for user, points in ((alice, 100), (bob, 100), (carol, 50)):
        models.UserRoundAnswer.objects.create(
            round=party_round, user=user, field="name", value="A", scored_points=points
        )
    models.UserRoundAnswer.objects.create(
        round=party_round, user=carol, field="city", value="x", scored_points=None
    )

    assert sorted(party_round.party.get_winners()) == ["alice", "bob"]


def test_players_without_points_score_zero_and_nobody_wins(party_factory, alice):
    party_round = models.PartyRound.objects.create(party=party_factory(), letter="A")
    models.UserRoundAnswer.objects.create(
        round=party_round, user=alice, field="name", value="x", scored_points=None
    )

    assert party_round.party.get_players_scores() == {"alice": 0}
    assert party_round.party.get_winners() == []


@pytest.fixture
def three_round_party(party_factory):
    return party_factory(max_rounds=3, max_round_duration=0, started_at=timezone.now())


@pytest.mark.django_db(transaction=True)
async def test_plays_exactly_max_rounds_and_closes_the_party(
    state_machine, instant_reveal, three_round_party
):
    await state_machine.play_party(three_round_party.id)

    await three_round_party.arefresh_from_db()
    assert await models.PartyRound.objects.filter(party=three_round_party).acount() == 3
    assert three_round_party.closed_at is not None


@pytest.mark.django_db(transaction=True)
async def test_broadcasts_the_final_results(
    state_machine, instant_reveal, channel_layer, receive_or_none, three_round_party
):
    channel = await channel_layer.new_channel()
    await channel_layer.group_add(f"party_{three_round_party.id}", channel)

    await state_machine.play_party(three_round_party.id)

    messages = []
    while message := await receive_or_none(channel):
        messages.append(message)
    assert "Partida terminada" in messages[-1]["message"]


@pytest.mark.django_db(transaction=True)
async def test_resumes_without_exceeding_max_rounds(
    state_machine, instant_reveal, three_round_party
):
    party_round = await models.PartyRound.objects.acreate(
        party=three_round_party, letter="A"
    )
    await party_round.close_round_and_calculate_scores()

    await state_machine.play_party(three_round_party.id)

    assert await models.PartyRound.objects.filter(party=three_round_party).acount() == 3


@pytest.mark.django_db(transaction=True)
async def test_restarting_does_not_touch_closed_parties(
    state_machine, instant_reveal, three_round_party
):
    await models.Party.objects.filter(id=three_round_party.id).aupdate(
        closed_at=timezone.now()
    )

    await state_machine.play_party(three_round_party.id)

    assert not await models.PartyRound.objects.filter(party=three_round_party).aexists()


def test_finished_party_shows_final_results_without_creating_rounds(
    logged_in_client, party_factory, alice
):
    party = party_factory(
        max_rounds=1,
        started_at=timezone.now(),
        closed_at=timezone.now(),
        joined_users=[alice],
    )
    models.PartyRound.objects.create(party=party, letter="A", closed_at=timezone.now())

    response = logged_in_client.get(reverse("detail_party", args=[party.id]))

    assertContains(response, "Partida terminada")
    assertNotContains(response, 'id="party_current_answers_form"')
    assert models.PartyRound.objects.filter(party=party).count() == 1
