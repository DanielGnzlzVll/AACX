"""Regenerate the downloaded word lists in core/lexicons/.

    uv run --no-project --with babel==2.18.0 --with xlrd==2.0.2 \\
        python scripts/build_lexicons.py

GeoNames, INE and kaikki.org publish only their latest data, so update the
retrieval date in core/lexicons/SOURCES.md after a rebuild. animal.txt,
color.txt and thing.txt are curated by hand and are not touched.
"""

import collections
import gzip
import io
import json
import re
import sys
import unicodedata
import urllib.request
import zipfile
from pathlib import Path

import xlrd
from babel import Locale

LEXICONS = Path(__file__).resolve().parent.parent / "core" / "lexicons"

GEONAMES = "https://download.geonames.org/export/dump"
INE_NAMES_URL = "https://www.ine.es/daco/daco42/nombyapel/nombres_por_edad_media.xls"
INE_SURNAMES_URL = "https://www.ine.es/daco/daco42/nombyapel/apellidos_frecuencia.xls"
WIKTIONARY_URL = "https://kaikki.org/eswiktionary/raw-wiktextract-data.jsonl.gz"

SPANISH_LOCALES = ["es", "es_419", "es_AR", "es_CO", "es_MX", "es_US"]
NOT_COUNTRIES = {"EU", "EZ", "QO", "UN", "XA", "XB", "ZZ"}
COUNTRY_VARIANTS = [
    "Bangladesh",
    "Birmania",
    "Botswana",
    "Corea",
    "Djibouti",
    "Escocia",
    "Estados Unidos de America",
    "Gales",
    "Holanda",
    "Hong Kong",
    "Inglaterra",
    "Iraq",
    "Irlanda del Norte",
    "Kenya",
    "Lesotho",
    "Macao",
    "Macedonia",
    "Malawi",
    "Palestina",
    "Qatar",
    "Republica Checa",
    "Rwanda",
    "Suazilandia",
    "Vaticano",
    "Zimbabwe",
]

MIN_NAME_FREQUENCY = 50
MIN_SURNAME_FREQUENCY = 100
MIN_POPULATION_FOR_EXONYMS = 1_000_000
LATIN = re.compile(r"^[^\W\d_]+(?:[ '.-][^\W\d_]+)*$")
WORD = re.compile(r"^[a-záéíóúüñ]+(?:[ -][a-záéíóúüñ]+)*$")
INFINITIVE = re.compile(r"^[a-záéíóúüñ]+(?:ar|er|ir|ír)$")

WIKTIONARY_POS = {"noun", "adj"}
WIKTIONARY_CATEGORIES = {
    "animal": {
        "ES:Anfibios",
        "ES:Animales",
        "ES:Animales extintos",
        "ES:Arácnidos",
        "ES:Aves",
        "ES:Crustáceos",
        "ES:Insectos",
        "ES:Mamíferos",
        "ES:Moluscos",
        "ES:Peces",
        "ES:Reptiles",
    },
    "color": {"ES:Colores"},
}


def is_latin(value):
    return bool(LATIN.match(value)) and all(
        unicodedata.name(char, "").startswith("LATIN") or not char.isalpha()
        for char in value
    )


def download(url):
    print(f"downloading {url}", file=sys.stderr)
    with urllib.request.urlopen(url, timeout=600) as response:
        return response.read()


def geonames_spanish_names(ids):
    archive = zipfile.ZipFile(io.BytesIO(download(f"{GEONAMES}/alternateNamesV2.zip")))
    names = collections.defaultdict(set)
    with archive.open("alternateNamesV2.txt") as rows:
        for row in io.TextIOWrapper(rows, encoding="utf-8"):
            fields = row.rstrip("\n").split("\t")
            colloquial_or_historic = "1" in fields[6:8]
            if fields[2] == "es" and fields[1] in ids and not colloquial_or_historic:
                names[fields[1]].add(fields[3])
    return names


def countries():
    names = set(COUNTRY_VARIANTS)
    for locale in SPANISH_LOCALES:
        for code, name in Locale.parse(locale).territories.items():
            if len(code) != 2 or not code.isalpha() or code in NOT_COUNTRIES:
                continue
            name = re.sub(r"^RAE de (.*) \(China\)$", r"\1", name)
            names.update(part.strip(" )") for part in name.split("("))
    return names


def cities(rows, spanish_names):
    names = set()
    for fields in rows:
        names.update(fields[1:3])
        names.update(spanish_names.get(fields[0], ()))
        if int(fields[14] or 0) >= MIN_POPULATION_FOR_EXONYMS:
            names.update(fields[3].split(","))
    return {name for name in names if is_latin(name)}


def ine_names(url, first_row, min_frequency, frequency_columns):
    book = xlrd.open_workbook(file_contents=download(url))
    names = set()
    for sheet in book.sheets():
        for index in range(first_row, sheet.nrows):
            row = sheet.row_values(index)
            frequencies = [row[column] for column in frequency_columns]
            if any(isinstance(f, float) and f >= min_frequency for f in frequencies):
                names.add(row[1])
    return names


def wiktionary():
    words, infinitives = set(), set()
    categorized = collections.defaultdict(set)
    with gzip.open(io.BytesIO(download(WIKTIONARY_URL)), "rt") as entries:
        for line in entries:
            entry = json.loads(line)
            if entry.get("lang_code") != "es":
                continue
            if entry.get("pos") == "verb":
                senses = entry.get("senses") or []
                if INFINITIVE.match(entry["word"]) and not any(
                    sense.get("form_of") for sense in senses
                ):
                    infinitives.add(entry["word"])
                continue
            if entry.get("pos") not in WIKTIONARY_POS:
                continue
            forms = [entry["word"]] + [
                form["form"]
                for form in entry.get("forms") or []
                if isinstance(form, dict) and form.get("form")
            ]
            words.update(form for form in forms if WORD.match(form))
            categories = set(map(str, entry.get("categories") or []))
            for sense in entry.get("senses") or []:
                categories.update(map(str, sense.get("categories") or []))
            for field, wanted in WIKTIONARY_CATEGORIES.items():
                if categories & wanted and WORD.match(entry["word"]):
                    categorized[field].add(entry["word"])
    return words, infinitives, categorized


def write(name, names):
    unique = {name.strip().casefold(): name.strip() for name in sorted(names)}
    unique.pop("", None)
    path = LEXICONS / f"{name}.txt"
    path.write_text("".join(f"{unique[key]}\n" for key in sorted(unique)))
    print(f"{path.name}: {len(unique)} entries", file=sys.stderr)


def main():
    archive = zipfile.ZipFile(io.BytesIO(download(f"{GEONAMES}/cities15000.zip")))
    city_rows = [
        line.split("\t")
        for line in archive.read("cities15000.txt").decode().splitlines()
    ]
    spanish_names = geonames_spanish_names({fields[0] for fields in city_rows})
    write("country", countries())
    write("city", cities(city_rows, spanish_names))
    write("name", ine_names(INE_NAMES_URL, 7, MIN_NAME_FREQUENCY, [2]))
    write("last_name", ine_names(INE_SURNAMES_URL, 5, MIN_SURNAME_FREQUENCY, [2, 3]))
    words, infinitives, categorized = wiktionary()
    write("word", words)
    write("verb", infinitives)
    for field, names in categorized.items():
        write(f"{field}-wiktionary", names)


if __name__ == "__main__":
    main()
