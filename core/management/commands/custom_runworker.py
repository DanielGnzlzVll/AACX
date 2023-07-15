from channels.management.commands.runworker import Command as RunworkerCommand

from core.routing import channel_routing


class Command(RunworkerCommand):
    def handle(self, *args, **options):
        if "*" in options["channels"]:
            options["channels"] = list(channel_routing.keys())
        super().handle(*args, **options)
