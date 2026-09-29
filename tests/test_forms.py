import pytest

from core.forms import CurrentAnswersForm
from core.models import PartyRound, UserRoundAnswer


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


def test_current_answers_form_rejects_answers_longer_than_model_field():
    max_length = UserRoundAnswer._meta.get_field("value").max_length
    form = CurrentAnswersForm(
        {"name": "M" * max_length, "city": "M" * (max_length + 1)},
        current_round=PartyRound(letter="M"),
    )

    assert not form.is_valid()
    assert list(form.errors) == ["city"]
    assert f'maxlength="{max_length}"' in str(form["city"])
