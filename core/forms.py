import re

from django import forms
from django.core.validators import RegexValidator
from django.utils.safestring import SafeString

from core import models


class NoRenderedWidget(forms.HiddenInput):
    def render(self, *args, **kwargs):
        return ""


class LoginForm(forms.Form):
    nickname = forms.CharField(
        label="Nombre de usuario",
        min_length=3,
        max_length=30,
        strip=True,
        validators=[
            RegexValidator(
                r"^[\w.-]+\Z",
                "Solo se permiten letras sin tildes ni ñ, números, puntos, guiones "
                "y guiones bajos.",
                flags=re.ASCII,
            )
        ],
        error_messages={
            "required": "Escribe un nombre de usuario.",
            "min_length": "El nombre de usuario debe tener al menos 3 caracteres.",
            "max_length": "El nombre de usuario debe tener máximo 30 caracteres.",
        },
        widget=forms.TextInput(attrs={"autofocus": True, "autocomplete": "off"}),
    )


class PartyForm(forms.ModelForm):
    class Meta:
        model = models.Party
        fields = [
            "name",
            "min_players",
            "max_round_duration",
            "max_rounds",
        ]
        error_messages = {
            field: {
                "required": "Este campo es obligatorio.",
                "invalid": "Escribe un número entero.",
                "min_value": "El valor debe ser mayor o igual a %(limit_value)s.",
                "max_value": "El valor debe ser menor o igual a %(limit_value)s.",
            }
            for field in ["min_players", "max_round_duration", "max_rounds"]
        }

    def clean_name(self):
        name = self.cleaned_data["name"]
        if models.Party.objects.filter(
            name__iexact=name, closed_at__isnull=True
        ).exists():
            raise forms.ValidationError(models.DUPLICATE_OPEN_PARTY_NAME_MESSAGE)
        return name


def answer_field(label):
    max_length = models.UserRoundAnswer._meta.get_field("value").max_length
    return forms.CharField(
        label=label,
        required=False,
        max_length=max_length,
        error_messages={
            "max_length": f"La respuesta debe tener máximo {max_length} caracteres.",
        },
        widget=forms.TextInput(attrs={"class": "input-answer"}),
    )


class CurrentAnswersForm(forms.Form):
    name = answer_field("Nombre")
    last_name = answer_field("Apellido")
    country = answer_field("País")
    city = answer_field("Ciudad")
    animal = answer_field("Animal")
    thing = answer_field("Cosa")
    color = answer_field("Color")

    # It is already in the template as button
    submit_stop = forms.BooleanField(
        widget=NoRenderedWidget(),
        required=False,
    )

    error_css_class = "word_column word-error"

    def __init__(self, *args, **kwargs):
        self.current_round = kwargs.pop("current_round")
        self.disabled = kwargs.pop("disabled", False)
        self.autofocus_name = kwargs.pop("autofocus_name", False)
        placeholder = self.current_round.letter.upper()

        super(CurrentAnswersForm, self).__init__(*args, **kwargs)

        for _, field in self.fields.items():
            field.widget.attrs["placeholder"] = placeholder
            field.widget.attrs["disabled"] = self.disabled

        if self.autofocus_name:
            self.fields["name"].widget.attrs["autofocus"] = True

    def as_div(self):
        return SafeString(
            super().as_div().replace("<div>", "<div class='word_column'>")
        )

    def clean(self):
        cleaned_data = super().clean().copy()

        if cleaned_data.get("submit_stop"):
            return cleaned_data

        for field, value in cleaned_data.items():
            if (
                value
                and type(value) is str
                and not models.answer_starts_with(value, self.current_round.letter)
            ):
                self.add_error(
                    field, f"'{value}' no empieza por '{self.current_round.letter}'"
                )
        return cleaned_data
