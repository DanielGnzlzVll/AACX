from django.db import migrations
from django.db.models import Count, F, Max, Q


def close_finished_parties(apps, schema_editor):
    Party = apps.get_model("core", "Party")
    finished_parties = (
        Party.objects.filter(started_at__isnull=False, closed_at__isnull=True)
        .annotate(
            closed_rounds=Count(
                "partyround", filter=Q(partyround__closed_at__isnull=False)
            ),
            last_closed_at=Max("partyround__closed_at"),
        )
        .filter(closed_rounds__gte=F("max_rounds"))
    )
    for party in finished_parties:
        party.closed_at = party.last_closed_at
        party.save(update_fields=["closed_at"])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0013_alter_party_max_rounds"),
    ]

    operations = [
        migrations.RunPython(close_finished_parties, migrations.RunPython.noop),
    ]
