# Generated by Django 4.2.3 on 2024-01-21 21:32

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0009_alter_userroundanswer_field'),
    ]

    operations = [
        migrations.AlterField(
            model_name='userroundanswer',
            name='field',
            field=models.CharField(choices=[('name', 'name'), ('last_name', 'last_name'), ('country', 'country'), ('city', 'city'), ('animal', 'animal'), ('thing', 'thing'), ('color', 'color')], max_length=50),
        ),
    ]