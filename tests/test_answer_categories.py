import re

from django.template.loader import render_to_string

from core.forms import CurrentAnswersForm
from core.models import AnswerVerdict, PartyRound, UserRoundAnswer

Category = UserRoundAnswer.Category


def test_categories_keep_their_stored_values_and_have_spanish_labels():
    assert Category.choices == [
        ("name", "Nombre"),
        ("last_name", "Apellido"),
        ("country", "País"),
        ("city", "Ciudad"),
        ("animal", "Animal"),
        ("thing", "Cosa"),
        ("color", "Color"),
    ]


def test_answer_models_accept_only_the_categories():
    for model in (UserRoundAnswer, AnswerVerdict):
        assert model._meta.get_field("field").choices == Category.choices


def test_answers_form_has_one_field_per_category():
    form = CurrentAnswersForm(current_round=PartyRound(letter="M"))

    assert [(field.name, field.label) for field in form.visible_fields()] == (
        Category.choices
    )


def test_answers_form_autofocuses_the_first_category():
    form = CurrentAnswersForm(current_round=PartyRound(letter="M"), autofocus_name=True)

    autofocused = [
        field.name
        for field in form.visible_fields()
        if field.field.widget.attrs.get("autofocus")
    ]
    assert autofocused == [Category.NAME]


def test_answers_table_has_a_column_per_category():
    html = render_to_string(
        "party_answers.html",
        {"rounds": [{"letter": "M", "name": "Maria", "thing": "Mesa"}]},
    )

    headers = re.findall(r"<th><strong>(.+?)</strong></th>", html)
    cells = re.findall(r"<td>(.*?)</td>", html)[1:]
    assert headers == ["Letra", *Category.labels]
    assert cells == ["Maria", "", "", "", "", "Mesa", ""]
    assert f'colspan="{len(Category) + 1}"' in render_to_string(
        "party_answers.html", {"rounds": []}
    )
