# /// script
# requires-python = ">=3.11"
# dependencies = ["playwright==1.63.0"]
# ///
"""Regenerate the README screenshots in docs/screenshots/.

Two players go through login, party creation, the waiting room, two rounds
with the answers reveal, and the final results.

Run it against an empty database, so the pages only show what the script
creates:

    docker compose down -v && docker compose up -d
    uv run scripts/screenshots.py

Without uv: pip install playwright==1.63.0, then python scripts/screenshots.py.
The first run also needs: playwright install chromium
"""

import argparse
import re
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "docs" / "screenshots"
DESKTOP = {"width": 1280, "height": 800}
MOBILE = {"width": 390, "height": 844}
TIMEOUT_MS = 60_000

FIELDS = ["name", "last_name", "country", "city", "animal", "thing", "color"]

ANSWERS = {
    "A": ["Ana", "Arango", "Argentina", "Asunción", "Ardilla", "Anillo", "Azul"],
    "B": ["Beatriz", "Bermúdez", "Brasil", "Bogotá", "Búho", "Balón", "Blanco"],
    "C": ["Carlos", "Castro", "Colombia", "Cali", "Conejo", "Cama", "Café"],
    "D": ["Daniel", "Díaz", "Dinamarca", "Dublín", "Delfín", "Dado", "Dorado"],
    "E": ["Elena", "Espinosa", "Ecuador", "Edimburgo", "Elefante", "Escoba", "Esmeralda"],
    "F": ["Fernando", "Flores", "Francia", "Florencia", "Foca", "Farol", "Fucsia"],
    "G": ["Gabriela", "Gómez", "Grecia", "Granada", "Gato", "Guitarra", "Gris"],
    "H": ["Hugo", "Herrera", "Honduras", "Helsinki", "Hormiga", "Hacha", "Hueso"],
    "I": ["Isabel", "Ibáñez", "Italia", "Ibagué", "Iguana", "Imán", "Índigo"],
    "J": ["Julia", "Jiménez", "Japón", "Jerusalén", "Jirafa", "Jarra", "Jade"],
    "K": ["Karla", "Klein", "Kenia", "Kioto", "Koala", "Kiosco", "Kaki"],
    "L": ["Laura", "López", "Líbano", "Lima", "León", "Lámpara", "Lila"],
    "M": ["María", "Martínez", "México", "Medellín", "Mono", "Mesa", "Morado"],
    "N": ["Nicolás", "Navarro", "Noruega", "Nápoles", "Nutria", "Nube", "Naranja"],
    "O": ["Óscar", "Ortiz", "Omán", "Oslo", "Oso", "Olla", "Ocre"],
    "P": ["Pablo", "Pérez", "Perú", "París", "Pato", "Pelota", "Púrpura"],
    "Q": ["Quintín", "Quintero", "Qatar", "Quito", "Quetzal", "Queso", ""],
    "R": ["Rosa", "Ramírez", "Rusia", "Roma", "Ratón", "Reloj", "Rojo"],
    "S": ["Sofía", "Sánchez", "Suecia", "Santiago", "Serpiente", "Silla", "Salmón"],
    "T": ["Tomás", "Torres", "Turquía", "Tokio", "Tigre", "Taza", "Turquesa"],
    "U": ["Úrsula", "Uribe", "Uruguay", "Utrecht", "Urraca", "Uva", "Ultramar"],
    "V": ["Valentina", "Vargas", "Venezuela", "Valencia", "Vaca", "Vaso", "Verde"],
    "W": ["Walter", "Williams", "", "Washington", "Wombat", "Wafle", ""],
    "X": ["Ximena", "Xirau", "", "Xalapa", "", "Xilófono", ""],
    "Y": ["Yolanda", "Yepes", "Yemen", "York", "Yak", "Yoyo", ""],
    "Z": ["Zoe", "Zapata", "Zambia", "Zaragoza", "Zorro", "Zapato", "Zafiro"],
}


def answers_for(letter, fields):
    row = dict(zip(FIELDS, ANSWERS[letter]))
    return {field: row[field] for field in fields}


def other_letter_answer(letter, field):
    letters = sorted(ANSWERS)
    other = letters[(letters.index(letter) + 1) % len(letters)]
    return answers_for(other, [field])[field] or "Mango"


def shoot(page: Page, name, full_page=False):
    page.mouse.move(0, 0)
    page.wait_for_timeout(400)
    page.screenshot(path=OUTPUT_DIR / f"{name}.png", full_page=full_page)
    print(f"docs/screenshots/{name}.png")


def navigate(page: Page, link_name):
    page.get_by_role("link", name=link_name, exact=True).click()
    page.wait_for_load_state("networkidle")


def login(page: Page, base_url, nickname):
    page.goto(f"{base_url}/login/")
    page.get_by_label("Nombre de usuario").fill(nickname)
    page.get_by_label("Nombre de usuario").press("Enter")
    page.wait_for_url(re.compile(r"/home/"))


def create_party(page: Page, name, min_players, rounds, submit_shot=None):
    navigate(page, "Crear nueva partida")
    page.locator("#id_name").fill(name)
    page.locator("#id_min_players").fill(str(min_players))
    page.locator("#id_max_round_duration").fill("120")
    page.locator("#id_max_rounds").fill(str(rounds))
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(800)
    if submit_shot:
        shoot(page, submit_shot, full_page=True)
    page.locator("#submit").click()
    expect(page.locator(".message")).to_be_visible()
    page.wait_for_load_state("networkidle")


def open_party(page: Page, name):
    page.locator("#content a", has_text=name).click()
    expect(page.locator("#party_content")).to_be_visible()


def current_letter(page: Page):
    return page.locator("#current_round_letter").inner_text().strip().upper()


def wait_for_round(page: Page, previous_letter=None):
    expect(page.locator("#party_current_answers_form input[name=name]")).to_be_enabled()
    if previous_letter:
        expect(page.locator("#current_round_letter")).not_to_have_text(previous_letter)
    return current_letter(page)


def fill_answers(page: Page, answers):
    form = page.locator("#party_current_answers_form")
    for field, value in answers.items():
        if value:
            form.locator(f"input[name={field}]").fill(value)
    page.wait_for_timeout(600)


def stop_and_capture_reveal(page: Page, shot_name=None):
    page.locator("#submit_stop").click()
    expect(page.locator("#modal_dialog[open]")).to_be_visible()
    if shot_name:
        expect(page.locator("#modal_dialog[open] h3")).to_have_text(
            re.compile("animal", re.IGNORECASE)
        )
        page.wait_for_timeout(1000)
        shoot(page, shot_name)
    expect(page.locator("#modal_dialog[open]")).to_have_count(0)


def run(base_url):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        ana_context = browser.new_context(viewport=DESKTOP, locale="es-CO")
        beto_context = browser.new_context(viewport=DESKTOP, locale="es-CO")
        for context in (ana_context, beto_context):
            context.set_default_timeout(TIMEOUT_MS)
            expect.set_options(timeout=TIMEOUT_MS)
        ana = ana_context.new_page()
        beto = beto_context.new_page()

        ana.goto(f"{base_url}/login/")
        ana.get_by_label("Nombre de usuario").fill("ana")
        shoot(ana, "01-login")
        login(ana, base_url, "ana")
        login(beto, base_url, "beto")

        create_party(beto, "Clase de español", min_players=4, rounds=3)
        create_party(
            ana, "Noche de juegos", min_players=2, rounds=2, submit_shot="03-create-party"
        )
        shoot(ana, "04-party-created")

        navigate(beto, "Inicio")
        shoot(beto, "02-home")

        open_party(ana, "Noche de juegos")
        expect(ana.locator("#party_content")).to_contain_text("1 de 2")
        shoot(ana, "05-waiting-room")

        open_party(beto, "Noche de juegos")
        letter = wait_for_round(ana)
        wait_for_round(beto)

        fill_answers(ana, answers_for(letter, FIELDS))
        beto_answers = answers_for(letter, ["name", "country", "thing"])
        beto_answers["animal"] = other_letter_answer(letter, "animal")
        fill_answers(beto, beto_answers)
        expect(beto.locator(".word-error")).to_have_count(1)
        shoot(beto, "07-incorrect-answer")

        stop_and_capture_reveal(ana, "08-answers-reveal")

        letter = wait_for_round(ana, previous_letter=letter)
        wait_for_round(beto)
        fill_answers(ana, answers_for(letter, ["name", "last_name", "country", "city"]))
        fill_answers(beto, answers_for(letter, ["name", "country", "animal", "color"]))
        shoot(ana, "06-game")

        ana.set_viewport_size(MOBILE)
        shoot(ana, "06-game-mobile", full_page=True)
        ana.set_viewport_size(DESKTOP)

        beto.locator("#party_points button", has_text="ana").click()
        expect(beto.locator("#modal_dialog[open]")).to_be_visible()
        beto.wait_for_timeout(1200)
        shoot(beto, "09-player-answers")
        beto.get_by_role("button", name="Cerrar").click()

        stop_and_capture_reveal(ana)
        expect(ana.locator("#party_finished")).to_be_visible()
        shoot(ana, "10-final-results", full_page=True)

        browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://localhost:8000")
    args = parser.parse_args()
    run(args.base_url.rstrip("/"))


if __name__ == "__main__":
    main()
