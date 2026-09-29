"""Regenerate the downloaded word lists in core/lexicons/.

    uv run --no-project --with babel --with wordfreq --with xlrd \
        python scripts/build_lexicons.py

animal.txt and color.txt are curated by hand and are not touched.
"""

import io
import re
import sys
import unicodedata
import urllib.request
import zipfile
from pathlib import Path

import wordfreq
import xlrd
from babel import Locale

LEXICONS = Path(__file__).resolve().parent.parent / "core" / "lexicons"

GEONAMES_URL = "https://download.geonames.org/export/dump/cities15000.zip"
INE_NAMES_URL = "https://www.ine.es/daco/daco42/nombyapel/nombres_por_edad_media.xls"
INE_SURNAMES_URL = "https://www.ine.es/daco/daco42/nombyapel/apellidos_frecuencia.xls"

SPANISH_LOCALES = ["es", "es_419", "es_AR", "es_CO", "es_MX", "es_US"]
NOT_COUNTRIES = {"EU", "EZ", "QO", "UN", "XA", "XB", "ZZ"}
COUNTRY_VARIANTS = [
    "Birmania",
    "Corea",
    "Escocia",
    "Estados Unidos de America",
    "Gales",
    "Holanda",
    "Hong Kong",
    "Inglaterra",
    "Irlanda del Norte",
    "Macao",
    "Macedonia",
    "Palestina",
    "Qatar",
    "Republica Checa",
    "Suazilandia",
    "Vaticano",
]

MIN_NAME_FREQUENCY = 50
MIN_SURNAME_FREQUENCY = 100
MIN_POPULATION_FOR_EXONYMS = 1_000_000
MIN_WORD_ZIPF = 2.5
LATIN = re.compile(r"^[^\W\d_]+(?:[ '.-][^\W\d_]+)*$")


def is_latin(value):
    return bool(LATIN.match(value)) and all(
        unicodedata.name(char, "").startswith("LATIN") or not char.isalpha()
        for char in value
    )


def download(url):
    print(f"downloading {url}", file=sys.stderr)
    with urllib.request.urlopen(url, timeout=300) as response:
        return response.read()


def countries():
    names = set(COUNTRY_VARIANTS)
    for locale in SPANISH_LOCALES:
        for code, name in Locale.parse(locale).territories.items():
            if len(code) != 2 or not code.isalpha() or code in NOT_COUNTRIES:
                continue
            name = re.sub(r"^RAE de (.*) \(China\)$", r"\1", name)
            names.update(part.strip(" )") for part in name.split("("))
    return names


def cities():
    archive = zipfile.ZipFile(io.BytesIO(download(GEONAMES_URL)))
    names = set()
    for line in archive.read("cities15000.txt").decode().splitlines():
        fields = line.split("\t")
        names.update(fields[1:3])
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


def words():
    return {
        word
        for word, frequency in wordfreq.get_frequency_dict("es", "large").items()
        if re.fullmatch("[a-záéíóúüñ]+", word)
        and wordfreq.freq_to_zipf(frequency) >= MIN_WORD_ZIPF
    }


def write(field, names):
    unique = {name.strip().casefold(): name.strip() for name in sorted(names)}
    unique.pop("", None)
    path = LEXICONS / f"{field}.txt"
    path.write_text("".join(f"{unique[key]}\n" for key in sorted(unique)))
    print(f"{path.name}: {len(unique)} entries", file=sys.stderr)


def main():
    write("country", countries())
    write("city", cities())
    write("name", ine_names(INE_NAMES_URL, 7, MIN_NAME_FREQUENCY, [2]))
    write("last_name", ine_names(INE_SURNAMES_URL, 5, MIN_SURNAME_FREQUENCY, [2, 3]))
    write("word", words())


if __name__ == "__main__":
    main()
