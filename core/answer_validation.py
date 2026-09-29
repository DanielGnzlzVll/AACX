import asyncio
import functools
import json
import logging
import urllib.request
from pathlib import Path

from asgiref.sync import sync_to_async
from django.conf import settings
from django.utils.module_loading import import_string

from core import models

logger = logging.getLogger(__name__)

LEXICONS = Path(__file__).resolve().parent / "lexicons"
Answer = models.UserRoundAnswer


@functools.cache
def load_lexicon(name):
    paths = [LEXICONS / f"{name}.txt", *LEXICONS.glob(f"{name}-*.txt")]
    words = [
        word
        for path in paths
        if path.exists()
        for word in path.read_text().splitlines()
    ]
    return frozenset(models.normalize_answer(word) for word in words) - {""}


FUNCTION_WORDS = frozenset(
    {"a", "al", "con", "de", "del", "el", "en", "la", "las", "los", "para", "y"}
)


def is_spanish_word(value):
    """True for a noun or adjective, alone or followed by other words.

    The words after the first may also be function words or infinitives, as in
    "máquina de coser".
    """
    head, *rest = value.split() or [""]
    words, verbs = load_lexicon("word"), load_lexicon("verb")
    return head in words and all(
        word in words or word in verbs or word in FUNCTION_WORDS for word in rest
    )


class LexiconValidator:
    """Accepts the words listed for their category.

    Misses are rejected in closed categories. In soft categories only misses
    that aren't Spanish words are rejected, since those lists can't be complete.
    Everything else is left to the next validator.
    """

    source = "lexicon"
    cache_verdicts = False
    closed_fields = frozenset(
        {
            Answer.NAME_CHOICE,
            Answer.LAST_NAME_CHOICE,
            Answer.COUNTRY_CHOICE,
            Answer.CITY_CHOICE,
        }
    )
    soft_fields = frozenset({Answer.ANIMAL_CHOICE, Answer.COLOR_CHOICE})

    def validate(self, pairs):
        verdicts = {}
        for field, value in pairs:
            if value in load_lexicon(field):
                verdicts[field, value] = True
            elif field in self.closed_fields:
                verdicts[field, value] = False
            elif field in self.soft_fields and not is_spanish_word(value):
                verdicts[field, value] = False
        return verdicts


class PartialLexiconValidator(LexiconValidator):
    """Only rejects unlisted countries, and leaves other misses to a model."""

    closed_fields = frozenset({Answer.COUNTRY_CHOICE})
    soft_fields = frozenset()


class DictionaryValidator:
    """Checks spelling against the Spanish nouns and adjectives of Wiktionary.

    It says nothing about the category, so it only judges categories that
    accept any common noun.
    """

    source = "dictionary"
    cache_verdicts = False
    fields = frozenset({Answer.THING_CHOICE})

    def validate(self, pairs):
        return {
            (field, value): is_spanish_word(value)
            for field, value in pairs
            if field in self.fields
        }


CATEGORY_LABELS = {
    Answer.NAME_CHOICE: "nombre de pila",
    Answer.LAST_NAME_CHOICE: "apellido",
    Answer.COUNTRY_CHOICE: "país",
    Answer.CITY_CHOICE: "ciudad",
    Answer.ANIMAL_CHOICE: "animal",
    Answer.THING_CHOICE: "cosa u objeto",
    Answer.COLOR_CHOICE: "color",
}

OLLAMA_SYSTEM_PROMPT = (
    "Eres el juez de un juego de palabras en español (Basta o Stop). Para cada "
    "respuesta decide si es una palabra real, bien escrita y de la categoría "
    "indicada. Las tildes y las mayúsculas se omiten a propósito: no las tengas "
    "en cuenta. Rechaza las faltas de ortografía, las palabras inventadas y las "
    "palabras de otra categoría."
)


class OllamaValidator:
    """Asks a local model for a yes/no verdict on every pair in one request."""

    cache_verdicts = True

    def __init__(self, url=None, model=None, timeout=None, options=None):
        self.url = url or settings.OLLAMA_URL
        self.model = model or settings.OLLAMA_MODEL
        self.timeout = timeout or settings.ANSWER_VALIDATION_TIMEOUT
        self.options = {"temperature": 0} | (options or {})

    @property
    def source(self):
        return f"ollama:{self.model}"

    def validate(self, pairs):
        pairs = sorted(pairs)
        keys = [str(index) for index in range(1, len(pairs) + 1)]
        prompt = "\n".join(
            f"{key}. {CATEGORY_LABELS[field]}: {value}"
            for key, (field, value) in zip(keys, pairs)
        )
        body = {
            "model": self.model,
            "stream": False,
            "keep_alive": -1,
            "options": self.options,
            "format": {
                "type": "object",
                "properties": {key: {"type": "boolean"} for key in keys},
                "required": keys,
            },
            "messages": [
                {"role": "system", "content": OLLAMA_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        }
        request = urllib.request.Request(
            f"{self.url}/api/chat",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            content = json.loads(json.load(response)["message"]["content"])
        return {
            pair: content[key]
            for key, pair in zip(keys, pairs)
            if isinstance(content.get(key), bool)
        }


def get_validators():
    return [import_string(path)() for path in settings.ANSWER_VALIDATORS if path]


async def avalidate(pairs, validators=None):
    """Maps each (field, normalized value) pair to True or False.

    Pairs missing from the result are unverified: every validator abstained,
    failed or ran out of time.
    """
    pairs = set(pairs)
    if not pairs:
        return {}
    verdicts = {
        (cached.field, cached.value): cached.is_valid
        async for cached in models.AnswerVerdict.objects.filter(
            value__in={value for _, value in pairs}
        )
        if (cached.field, cached.value) in pairs
    }
    loop = asyncio.get_running_loop()
    deadline = loop.time() + settings.ANSWER_VALIDATION_TIMEOUT
    for validator in get_validators() if validators is None else validators:
        pending = pairs - verdicts.keys()
        if not pending:
            break
        try:
            async with asyncio.timeout_at(deadline):
                found = await sync_to_async(validator.validate, thread_sensitive=False)(
                    pending
                )
        except TimeoutError:
            logger.warning(f"{validator.source} timed out on {len(pending)} answers")
            break
        except Exception:
            logger.exception(f"{validator.source} failed on {len(pending)} answers")
            continue
        found = {pair: found[pair] for pair in pending & found.keys()}
        verdicts |= found
        if validator.cache_verdicts and found:
            await models.AnswerVerdict.objects.abulk_create(
                [
                    models.AnswerVerdict(
                        field=field,
                        value=value,
                        is_valid=is_valid,
                        source=validator.source,
                    )
                    for (field, value), is_valid in found.items()
                ],
                ignore_conflicts=True,
            )
    return verdicts


async def avalidate_round(round):
    letter = models.normalize_answer(round.letter)
    pairs = set()
    async for field, value in models.UserRoundAnswer.objects.filter(
        round=round
    ).values_list("field", "value"):
        normalized = models.normalize_answer(value)
        if normalized.startswith(letter):
            pairs.add((field, normalized))
    return await avalidate(pairs)
