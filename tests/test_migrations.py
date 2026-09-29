import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

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
