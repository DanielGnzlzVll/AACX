# Word list sources

`scripts/build_lexicons.py` generates every list except `animal.txt`, `color.txt` and `thing.txt`, which are curated by hand for this project. The downloaded sources were retrieved on 2026-09-29.

| File | Source | License |
|---|---|---|
| `country.txt` | Spanish territory names from the [Unicode CLDR](https://cldr.unicode.org/) 47, through [Babel](https://babel.pocoo.org/) 2.18.0, plus the variants listed in the script | [Unicode License v3](LICENSE-UNICODE.txt) |
| `city.txt` | [GeoNames](https://www.geonames.org/) `cities15000` and the Spanish names in `alternateNamesV2` | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |
| `name.txt`, `last_name.txt` | Instituto Nacional de Estadística (INE), *Frecuencias de nombres* (Padrón continuo, 1 January 2022) and *Frecuencias de apellidos* (Censo anual, 1 January 2025). Fuente: INE. | [INE reuse terms](https://www.ine.es/aviso_legal): reuse is allowed citing INE as the source |
| `word.txt`, `verb.txt`, `animal-wiktionary.txt`, `color-wiktionary.txt` | Spanish entries of the [Spanish Wiktionary](https://es.wiktionary.org/), extracted by [Wiktextract](https://github.com/tatuylonen/wiktextract) and published by [kaikki.org](https://kaikki.org/eswiktionary/) | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). These files are adaptations and are distributed under the same license. |
