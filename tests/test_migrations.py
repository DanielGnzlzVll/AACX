import importlib

import pytest
from django.apps import apps
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from core.models import Party, PartyRound

BEFORE = [("core", "0016_party_created_by_unique_open_name")]
AFTER = [("core", "0017_party_settings_not_null")]


def migrate(targets):
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(targets)
    return executor.loader.project_state(targets).apps


@pytest.mark.django_db(transaction=True)
def test_party_settings_migration_backfills_nulls_and_clamps_out_of_range():
    old_apps = migrate(BEFORE)
    Party = old_apps.get_model("core", "Party")
    nulls = Party.objects.create(
        name="nulls", min_players=None, max_round_duration=None, max_rounds=3
    )
    out_of_range = Party.objects.create(
        name="out of range", min_players=50, max_round_duration=100000, max_rounds=3
    )
    valid = Party.objects.create(
        name="valid", min_players=4, max_round_duration=90, max_rounds=7
    )

    try:
        Party = migrate(AFTER).get_model("core", "Party")

        settings = {
            party.pk: (party.min_players, party.max_round_duration, party.max_rounds)
            for party in Party.objects.all()
        }
        assert settings == {
            nulls.pk: (2, 120, 3),
            out_of_range.pk: (20, 600, 3),
            valid.pk: (4, 90, 7),
        }
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())


@pytest.mark.django_db
def test_close_finished_parties_closes_only_parties_that_played_all_their_rounds(
    party_factory,
):
    migration = importlib.import_module("core.migrations.0014_close_finished_parties")
    last_closed_at = timezone.now()
    finished = party_factory(max_rounds=2, started_at=timezone.now())
    PartyRound.objects.create(
        party=finished,
        letter="A",
        closed_at=last_closed_at - timezone.timedelta(minutes=1),
    )
    PartyRound.objects.create(party=finished, letter="B", closed_at=last_closed_at)
    in_progress = party_factory(max_rounds=2, started_at=timezone.now())
    PartyRound.objects.create(party=in_progress, letter="A", closed_at=timezone.now())
    PartyRound.objects.create(party=in_progress, letter="B")

    migration.close_finished_parties(apps, None)

    finished.refresh_from_db()
    in_progress.refresh_from_db()
    assert finished.closed_at == last_closed_at
    assert in_progress.closed_at is None


@pytest.mark.django_db
def test_rename_duplicate_open_parties_keeps_only_the_oldest_name(party_factory):
    migration = importlib.import_module(
        "core.migrations.0016_party_created_by_unique_open_name"
    )
    with connection.schema_editor() as editor:
        for constraint in Party._meta.constraints:
            editor.remove_constraint(Party, constraint)
    oldest = party_factory(name="hijack")
    duplicate = party_factory(name="HIJACK")
    closed = party_factory(name="hijack", closed_at=timezone.now())
    other = party_factory(name="other")
    long_name = "x" * 50
    party_factory(name=long_name)
    long_duplicate = party_factory(name=long_name)

    migration.rename_duplicate_open_parties(apps, None)

    names = dict(Party.objects.values_list("pk", "name"))
    assert names[oldest.pk] == "hijack"
    assert names[duplicate.pk] == f"HIJACK ({duplicate.pk})"
    assert names[closed.pk] == "hijack"
    assert names[other.pk] == "other"
    suffix = f" ({long_duplicate.pk})"
    assert names[long_duplicate.pk] == long_name[: 50 - len(suffix)] + suffix
