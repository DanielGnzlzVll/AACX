from django.db import migrations, models


def mark_closed_parties_finished(apps, schema_editor):
    Party = apps.get_model("core", "Party")
    Party.objects.filter(closed_at__isnull=False).update(closed_reason="finished")


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0022_partyround_number_closed_reason"),
    ]

    operations = [
        migrations.AddField(
            model_name="party",
            name="closed_reason",
            field=models.CharField(
                blank=True,
                choices=[("finished", "Terminada"), ("abandoned", "Abandonada")],
                max_length=10,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="party",
            name="last_seen_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(mark_closed_parties_finished, migrations.RunPython.noop),
        migrations.AddField(
            model_name="party",
            name="status",
            field=models.GeneratedField(
                choices=[
                    ("waiting", "Esperando jugadores"),
                    ("playing", "En curso"),
                    ("finished", "Terminada"),
                    ("abandoned", "Abandonada"),
                ],
                db_persist=True,
                expression=models.Case(
                    models.When(
                        closed_reason="abandoned", then=models.Value("abandoned")
                    ),
                    models.When(closed_at__isnull=False, then=models.Value("finished")),
                    models.When(started_at__isnull=False, then=models.Value("playing")),
                    default=models.Value("waiting"),
                ),
                output_field=models.CharField(max_length=10),
            ),
        ),
    ]
