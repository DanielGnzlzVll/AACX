from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0023_party_status_abandoned"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="party",
            name="waiting_started_at",
        ),
    ]
