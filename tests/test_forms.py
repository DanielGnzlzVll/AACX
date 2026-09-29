import pytest

from core.forms import CurrentAnswersForm
from core.models import PartyRound


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

