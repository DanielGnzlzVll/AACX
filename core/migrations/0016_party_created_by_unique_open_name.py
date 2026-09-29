from django.conf import settings
from django.db import migrations, models
from django.db.models.functions import Lower
import django.db.models.deletion


def rename_duplicate_open_parties(apps, schema_editor):
    Party = apps.get_model("core", "Party")
    seen = set()
    for party in Party.objects.filter(closed_at__isnull=True).order_by("pk"):
        key = party.name.lower()
        if key not in seen:
            seen.add(key)
            continue
        suffix = f" ({party.pk})"
        party.name = party.name[: 50 - len(suffix)] + suffix
        party.save(update_fields=["name"])


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("core", "0015_party_waiting_started_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="party",
            name="created_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="created_parties",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RunPython(rename_duplicate_open_parties, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="party",
            constraint=models.UniqueConstraint(
                Lower("name"),
                condition=models.Q(("closed_at__isnull", True)),
                name="unique_open_party_name",
                violation_error_message="Ya existe una partida abierta con ese nombre.",
            ),
        ),
    ]
