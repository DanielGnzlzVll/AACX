from html.parser import HTMLParser

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.models import PartyRound, UserRoundAnswer

VOID_ELEMENTS = {"meta", "link", "input", "br", "hr", "img"}


class Element:
    def __init__(self, tag, attrs, parent=None):
        self.tag = tag
        self.attrs = dict(attrs)
        self.parent = parent
        self.children = []

    def iter(self):
        for child in self.children:
            yield child
            yield from child.iter()


class Tree(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.root = self.current = Element("#document", [])
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        element = Element(tag, attrs, self.current)
        self.current.children.append(element)
        if tag not in VOID_ELEMENTS:
            self.current = element

    def handle_endtag(self, tag):
        element = self.current
        while element.parent and element.tag != tag:
            element = element.parent
        if element.parent:
            self.current = element.parent


@pytest.fixture
def page(logged_in_client, party_factory, alice, bob):
    party = party_factory(started_at=timezone.now(), joined_users=[alice, bob])
    closed_round = PartyRound.objects.create(
        party=party, letter="A", closed_at=timezone.now()
    )
    UserRoundAnswer.objects.create(
        round=closed_round, user=alice, field="name", value="Ana", scored_points=100
    )
    PartyRound.objects.create(party=party, letter="B")
    party_factory(joined_users=[alice])
    started_without_alice = party_factory(started_at=timezone.now(), joined_users=[bob])
    urls = {
        "login": (Client(), reverse("login")),
        "home": (logged_in_client, reverse("home")),
        "create_party": (logged_in_client, reverse("create_party")),
        "party": (
            logged_in_client,
            reverse("detail_party", kwargs={"party_id": party.id}),
        ),
        "party_started": (
            logged_in_client,
            reverse("detail_party", kwargs={"party_id": started_without_alice.id}),
        ),
    }

    def render(name):
        client, url = urls[name]
        response = client.get(url)
        assert response.status_code == 200
        return Tree(response.content.decode()).root

    return render


PAGES = ["login", "home", "create_party", "party", "party_started"]


@pytest.mark.parametrize("name", PAGES)
def test_document_has_head_and_body(page, name):
    (html,) = [el for el in page(name).children if el.tag == "html"]

    assert [child.tag for child in html.children] == ["head", "body"]
    head_tags = {child.tag for child in html.children[0].children}
    assert {"meta", "title", "link", "script"} <= head_tags


@pytest.mark.parametrize("name", PAGES)
def test_lists_only_contain_list_items(page, name):
    for element in page(name).iter():
        if element.tag in ("ul", "ol"):
            assert {child.tag for child in element.children} <= {"li"}
        if element.tag == "nav":
            assert {child.tag for child in element.children} <= {"ul"}


@pytest.mark.parametrize("name", PAGES)
def test_no_inline_styles(page, name):
    assert not [el.tag for el in page(name).iter() if "style" in el.attrs]


@pytest.mark.parametrize("name", PAGES)
def test_labels_point_to_existing_ids(page, name):
    elements = list(page(name).iter())
    ids = {el.attrs["id"] for el in elements if "id" in el.attrs}

    assert {el.attrs["for"] for el in elements if el.tag == "label"} <= ids


def test_score_rows_are_keyboard_accessible(page):
    rows = [
        el for el in page("party").iter() if el.tag == "tr" and "hx-get" in el.attrs
    ]

    assert rows
    for row in rows:
        buttons = [el for el in row.iter() if el.tag == "button"]
        assert [button.attrs.get("type") for button in buttons] == ["button"]


@pytest.mark.parametrize("name", ["home", "create_party", "party"])
def test_modal_survives_htmx_navigation(page, name):
    elements = {el.attrs.get("id"): el for el in page(name).iter()}

    modal = elements["modal"]
    while modal.parent:
        modal = modal.parent
        assert modal is not elements["content"]
