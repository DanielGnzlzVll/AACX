import pytest

from core.forms import CurrentAnswersForm, PartyForm
from core.models import PartyRound, UserRoundAnswer

VALID_PARTY_DATA = {
    "name": "new party",
    "min_players": 2,
    "max_round_duration": 120,
    "max_rounds": 5,
}


@pytest.mark.parametrize(
    "data, errors",
    [
        ({"name": "Maria"}, {}),
        ({"name": "maria"}, {}),
        ({"name": "Maria", "animal": "Mono", "color": "morado"}, {}),
        ({}, {}),
        ({"name": ""}, {}),
        ({"name": "Pedro"}, {"name": ["'Pedro' no empieza por 'M'"]}),
        (
            {"name": "Maria", "city": "Bogota", "thing": "Tijera"},
            {
                "city": ["'Bogota' no empieza por 'M'"],
                "thing": ["'Tijera' no empieza por 'M'"],
            },
        ),
        ({"name": "Pedro", "submit_stop": "true"}, {}),
    ],
)
def test_current_answers_form_letter_validation(data, errors):
    form = CurrentAnswersForm(data, current_round=PartyRound(letter="M"))

    assert form.is_valid() == (not errors)
    assert form.errors == errors


@pytest.mark.parametrize(
    "letter, value, valid",
    [
        ("A", "Álvaro", True),
        ("A", "Ávila", True),
        ("A", "álvaro", True),
        ("A", "ÁNGEL", True),
        ("A", "  ana  ", True),
        ("E", "Éxito", True),
        ("I", "Íñigo", True),
        ("O", "Óscar", True),
        ("U", "Úrsula", True),
        ("U", "Ürümqi", True),
        ("N", "Ñandú", False),
        ("N", "ñu", False),
        ("B", "Álvaro", False),
    ],
)
def test_current_answers_form_letter_check_ignores_case_accents_and_spaces(
    letter, value, valid
):
    form = CurrentAnswersForm({"name": value}, current_round=PartyRound(letter=letter))

    assert form.is_valid() == valid


def test_current_answers_form_rejects_answers_longer_than_model_field():
    max_length = UserRoundAnswer._meta.get_field("value").max_length
    form = CurrentAnswersForm(
        {"name": "M" * max_length, "city": "M" * (max_length + 1)},
        current_round=PartyRound(letter="M"),
    )

    assert not form.is_valid()
    assert list(form.errors) == ["city"]
    assert f'maxlength="{max_length}"' in str(form["city"])


@pytest.mark.parametrize(
    "field, low, high",
    [
        ("min_players", 2, 20),
        ("max_round_duration", 30, 600),
        ("max_rounds", 1, 26),
    ],
)
def test_party_form_accepts_settings_within_bounds(db, field, low, high):
    for value in (low, high):
        form = PartyForm(VALID_PARTY_DATA | {field: value})

        assert form.is_valid(), form.errors


@pytest.mark.parametrize(
    "field, value, error",
    [
        ("min_players", "", "Este campo es obligatorio."),
        ("min_players", "abc", "Escribe un número entero."),
        ("min_players", 1, "El valor debe ser mayor o igual a 2."),
        ("min_players", 21, "El valor debe ser menor o igual a 20."),
        ("max_round_duration", "", "Este campo es obligatorio."),
        ("max_round_duration", 29, "El valor debe ser mayor o igual a 30."),
        ("max_round_duration", 601, "El valor debe ser menor o igual a 600."),
        ("max_rounds", "", "Este campo es obligatorio."),
        ("max_rounds", 0, "El valor debe ser mayor o igual a 1."),
        ("max_rounds", 27, "El valor debe ser menor o igual a 26."),
    ],
)
def test_party_form_rejects_blank_or_out_of_range_settings(db, field, value, error):
    form = PartyForm(VALID_PARTY_DATA | {field: value})

    assert not form.is_valid()
    assert form.errors == {field: [error]}


def test_party_form_requires_every_setting(db):
    form = PartyForm({"name": "new party"})

    assert not form.is_valid()
    assert set(form.errors) == {"min_players", "max_round_duration", "max_rounds"}
