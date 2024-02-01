from django import forms
from django.utils.safestring import SafeString


class NoRenderedWidget(forms.HiddenInput):
    def render(self, *args, **kwargs):
        return ""


class CurrentAnswersForm(forms.Form):
    name = forms.CharField(
        label="Nombre",
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "input-answer",
            }
        ),
    )
    last_name = forms.CharField(
        label="Apellido",
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "input-answer",
            }
        ),
    )
    country = forms.CharField(
        label="País",
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "input-answer",
            }
        ),
    )
    city = forms.CharField(
        label="Ciudad",
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "input-answer",
            }
        ),
    )
    animal = forms.CharField(
        label="Animal",
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "input-answer",
            }
        ),
    )
    thing = forms.CharField(
        label="Cosa",
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "input-answer",
            }
        ),
    )
    color = forms.CharField(
        label="Color",
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "input-answer",
            }
        ),
    )

    # It is already in the template as button
    submit_stop = forms.BooleanField(
        widget=NoRenderedWidget(),
        required=False,
    )

    error_css_class = "word_column word-error"

    def __init__(self, *args, **kwargs):
        self.current_round = kwargs.pop("current_round")
        self.disabled = kwargs.pop("disabled", False)
        placeholder = self.current_round.letter.upper()

        super(CurrentAnswersForm, self).__init__(*args, **kwargs)

        for _, field in self.fields.items():
            field.widget.attrs["placeholder"] = placeholder
            field.widget.attrs["disabled"] = self.disabled

    def as_div(self):
        return SafeString(
            super().as_div().replace("<div>", "<div class='word_column'>")
        )

    def clean(self):
        cleaned_data = super().clean().copy()

        for field, value in cleaned_data.items():
            if (
                value
                and type(value) is str
                and not cleaned_data[field]
                .lower()
                .startswith(self.current_round.letter.lower())
            ):
                self.add_error(
                    field, f"'{value}' no empieza por '{self.current_round.letter}'"
                )
        return cleaned_data
