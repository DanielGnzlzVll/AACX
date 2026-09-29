import io
import json
import time

import pytest
from asgiref.sync import async_to_sync, sync_to_async
from django.db import connections
from django.template.loader import render_to_string

from asacx import settings as base_settings
from core import answer_validation, consumers
from core.models import AnswerVerdict, PartyRound, UserRoundAnswer

Verdict = UserRoundAnswer.Verdict


class FakeValidator:
    source = "fake"
    cache_verdicts = False

    def __init__(self, verdicts=None, delay=0, error=None, cache_verdicts=False):
        self.verdicts = verdicts or {}
        self.delay = delay
        self.error = error
        self.cache_verdicts = cache_verdicts
        self.calls = []

    def validate(self, pairs):
        self.calls.append(set(pairs))
        time.sleep(self.delay)
        if self.error:
            raise self.error
        return dict(self.verdicts)


@pytest.fixture
def validators(monkeypatch):
    configured = []
    monkeypatch.setattr(answer_validation, "get_validators", lambda: configured)
    return configured


def validate(pairs, validators=None):
    return async_to_sync(answer_validation.avalidate)(pairs, validators)


@pytest.mark.parametrize(
    "pair, expected",
    [
        (("country", "colombia"), {("country", "colombia"): True}),
        (("country", "peru"), {("country", "peru"): True}),
        (("country", "colonbia"), {("country", "colonbia"): False}),
        (("city", "bogota"), {("city", "bogota"): True}),
        (("city", "bogta"), {("city", "bogta"): False}),
        (("name", "ana"), {("name", "ana"): True}),
        (("name", "mesa"), {("name", "mesa"): False}),
        (("last_name", "quintero"), {("last_name", "quintero"): True}),
        (("animal", "ñandu"), {("animal", "ñandu"): True}),
        (("animal", "elefnte"), {("animal", "elefnte"): False}),
        (("color", "fucsia"), {("color", "fucsia"): True}),
        (("thing", "mesa"), {}),
    ],
)
def test_lexicon_validator(pair, expected):
    assert answer_validation.LexiconValidator().validate({pair}) == expected


@pytest.mark.parametrize(
    "pair, expected",
    [
        (("country", "colonbia"), {("country", "colonbia"): False}),
        (("city", "bogota"), {("city", "bogota"): True}),
        (("city", "bogta"), {}),
        (("animal", "elefnte"), {}),
    ],
)
def test_partial_lexicon_validator_leaves_misses_to_the_next(pair, expected):
    assert answer_validation.PartialLexiconValidator().validate({pair}) == expected


def test_default_validators_need_no_model(settings):
    settings.ANSWER_VALIDATORS = base_settings.ANSWER_VALIDATORS

    assert [type(v) for v in answer_validation.get_validators()] == [
        answer_validation.LexiconValidator,
        answer_validation.DictionaryValidator,
    ]


def test_empty_validator_paths_are_skipped(settings):
    settings.ANSWER_VALIDATORS = [""]

    assert answer_validation.get_validators() == []


@pytest.mark.parametrize(
    "pair, expected",
    [
        (("thing", "mesa"), {("thing", "mesa"): True}),
        (("thing", "lapiz"), {("thing", "lapiz"): True}),
        (("thing", "maquina de coser"), {("thing", "maquina de coser"): True}),
        (("thing", "mesaa"), {("thing", "mesaa"): False}),
        (("thing", "asdf"), {("thing", "asdf"): False}),
        (("animal", "mesaa"), {}),
    ],
)
def test_dictionary_validator(pair, expected):
    assert answer_validation.DictionaryValidator().validate({pair}) == expected


def test_later_validators_only_get_undecided_pairs(db):
    first = FakeValidator({("thing", "mesa"): True})
    second = FakeValidator({("thing", "mesaa"): False})

    verdicts = validate({("thing", "mesa"), ("thing", "mesaa")}, [first, second])

    assert verdicts == {("thing", "mesa"): True, ("thing", "mesaa"): False}
    assert second.calls == [{("thing", "mesaa")}]


def test_verdicts_for_pairs_not_asked_are_ignored(db):
    validator = FakeValidator({("thing", "mesa"): True, ("thing", "otra"): False})

    assert validate({("thing", "mesa")}, [validator]) == {("thing", "mesa"): True}


def test_stored_verdicts_win_over_validators(db):
    AnswerVerdict.objects.create(field="country", value="Colonbia", is_valid=True)
    validator = FakeValidator({("country", "colonbia"): False})

    verdicts = validate({("country", "colonbia")}, [validator])

    assert verdicts == {("country", "colonbia"): True}
    assert validator.calls == []


def test_stored_verdicts_are_per_category(db):
    AnswerVerdict.objects.create(field="animal", value="mesa", is_valid=False)

    assert validate({("thing", "mesa")}, []) == {}


def test_only_cacheable_verdicts_are_stored(db):
    lexicon = FakeValidator({("thing", "mesa"): True})
    model = FakeValidator({("thing", "mesaa"): False}, cache_verdicts=True)

    validate({("thing", "mesa"), ("thing", "mesaa")}, [lexicon, model])

    assert list(AnswerVerdict.objects.values_list("field", "value", "is_valid")) == [
        ("thing", "mesaa", False)
    ]


def test_a_failing_validator_leaves_its_pairs_to_the_next(db):
    broken = FakeValidator(error=ConnectionRefusedError())
    fallback = FakeValidator({("thing", "mesa"): True})

    assert validate({("thing", "mesa")}, [broken, fallback]) == {
        ("thing", "mesa"): True
    }


def test_timeout_keeps_earlier_verdicts_and_leaves_the_rest_unverified(db, settings):
    settings.ANSWER_VALIDATION_TIMEOUT = 0.2
    fast = FakeValidator({("thing", "mesa"): True})
    slow = FakeValidator({("thing", "mesaa"): False}, delay=2)

    started = time.monotonic()
    verdicts = validate({("thing", "mesa"), ("thing", "mesaa")}, [fast, slow])

    assert time.monotonic() - started < 1
    assert verdicts == {("thing", "mesa"): True}


def test_no_pairs_skip_every_validator(db):
    validator = FakeValidator()

    assert validate(set(), [validator]) == {}
    assert validator.calls == []


def test_answer_verdict_stores_the_normalized_value(db):
    verdict = AnswerVerdict.objects.create(
        field="city", value=" Bogotá ", is_valid=True
    )

    assert verdict.value == "bogota"


def test_ollama_validator_sends_one_request_and_maps_the_answer(monkeypatch):
    requests = []

    def urlopen(request, timeout):
        requests.append((json.loads(request.data), timeout))
        content = json.dumps({"1": True, "2": False})
        return io.BytesIO(json.dumps({"message": {"content": content}}).encode())

    monkeypatch.setattr(answer_validation.urllib.request, "urlopen", urlopen)
    validator = answer_validation.OllamaValidator(
        url="http://ollama:11434", model="tiny", timeout=3
    )

    verdicts = validator.validate({("thing", "mesa"), ("animal", "mesa")})

    assert verdicts == {("animal", "mesa"): True, ("thing", "mesa"): False}
    [(body, timeout)] = requests
    assert timeout == 3
    assert body["model"] == "tiny"
    assert body["format"]["required"] == ["1", "2"]
    assert body["messages"][1]["content"] == "1. animal: mesa\n2. cosa u objeto: mesa"


def test_ollama_validator_skips_keys_missing_from_the_answer(monkeypatch):
    def urlopen(request, timeout):
        content = json.dumps({"1": "yes"})
        return io.BytesIO(json.dumps({"message": {"content": content}}).encode())

    monkeypatch.setattr(answer_validation.urllib.request, "urlopen", urlopen)

    assert (
        answer_validation.OllamaValidator(url="http://ollama", model="m").validate(
            {("thing", "mesa")}
        )
        == {}
    )


def create_round(party_factory, user_factory, letter, answers):
    round = PartyRound.objects.create(party=party_factory(), letter=letter)
    for username, field, value in answers:
        UserRoundAnswer.objects.create(
            round=round, user=user_factory(username), field=field, value=value
        )
    return round


def test_round_validation_sends_unique_normalized_answers_with_the_letter(
    validators, party_factory, alice, bob, user_factory
):
    round = PartyRound.objects.create(party=party_factory(), letter="M")
    for user, field, value in [
        (alice, "thing", "Mesa"),
        (bob, "thing", " mesa "),
        (alice, "animal", "Mono"),
        (bob, "animal", "Perro"),
        (alice, "color", ""),
    ]:
        UserRoundAnswer.objects.create(round=round, user=user, field=field, value=value)
    validator = FakeValidator()
    validators.append(validator)

    async_to_sync(answer_validation.avalidate_round)(round)

    assert validator.calls == [{("thing", "mesa"), ("animal", "mono")}]


def score(round, verdicts):
    async_to_sync(round.close_round_and_calculate_scores)(verdicts)
    return {
        answer.user.username: (answer.scored_points, answer.verdict)
        for answer in UserRoundAnswer.objects.filter(round=round)
    }


def test_invalid_answers_score_zero_and_do_not_share_points(
    party_factory, user_factory
):
    round = create_round(
        party_factory,
        user_factory,
        "M",
        [
            ("alice", "thing", "Mesaa"),
            ("bob", "thing", "mesaa"),
            ("carol", "thing", "Mesa"),
            ("dave", "thing", "Mesa"),
        ],
    )

    scored = score(round, {("thing", "mesaa"): False, ("thing", "mesa"): True})

    assert scored == {
        "alice": (0, Verdict.INVALID),
        "bob": (0, Verdict.INVALID),
        "carol": (50, Verdict.VALID),
        "dave": (50, Verdict.VALID),
    }


def test_unverified_answers_are_accepted(party_factory, user_factory):
    round = create_round(
        party_factory,
        user_factory,
        "M",
        [("alice", "thing", "Mesa"), ("bob", "thing", "Pato"), ("carol", "thing", "")],
    )

    assert score(round, {}) == {
        "alice": (100, Verdict.UNVERIFIED),
        "bob": (0, None),
        "carol": (0, None),
    }


def test_a_verdict_only_applies_to_its_category(party_factory, user_factory):
    round = create_round(
        party_factory,
        user_factory,
        "M",
        [("alice", "thing", "Mono"), ("bob", "animal", "Mono")],
    )

    assert score(round, {("thing", "mono"): False, ("animal", "mono"): True}) == {
        "alice": (0, Verdict.INVALID),
        "bob": (100, Verdict.VALID),
    }


@pytest.mark.django_db(transaction=True)
async def test_state_machine_rejects_invalid_answers_in_the_reveal(
    validators, channel_layer, party_factory, user_factory, monkeypatch
):
    round = await sync_to_async(create_round)(
        party_factory,
        user_factory,
        "M",
        [("alice", "thing", "Mesaa"), ("bob", "thing", "Mesa")],
    )
    validators.append(FakeValidator({("thing", "mesaa"): False}))
    machine = consumers.PartyStateMachine()
    machine.channel_layer = channel_layer
    revealed = []

    async def display_all_answers(answers, current_round, party):
        revealed.extend(
            [(answer.value, answer.scored_points, answer.verdict) for answer in answers]
        )

    monkeypatch.setattr(machine, "display_all_answers", display_all_answers)

    party = await sync_to_async(lambda: round.party)()
    await machine.update_scores(party, round)
    await sync_to_async(connections.close_all)()

    assert sorted(revealed) == [
        ("Mesa", 100, Verdict.UNVERIFIED),
        ("Mesaa", 0, Verdict.INVALID),
    ]


def test_reveal_modal_marks_rejected_answers():
    html = render_to_string(
        "party_current_all_users_answers_modal.html",
        {
            "answers": [
                {
                    "username": "alice",
                    "value": "Mesaa",
                    "scored_points": 0,
                    "rejected": True,
                },
                {
                    "username": "bob",
                    "value": "Mesa",
                    "scored_points": 100,
                    "rejected": False,
                },
            ],
        },
    )

    assert "<del>Mesaa</del>" in html
    assert html.count("No válida") == 1
    assert "<td>Mesa</td>" in html
