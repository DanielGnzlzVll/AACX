# 0002. Run the party lifecycle as a Channels worker

## Status

Accepted, to be superseded by [0004](0004-persisted-event-driven-party-state-machine.md)

## Context

A party needs a server-side clock and a single authority. Someone has to wait for players, pick a letter, start the round, end it on timeout or STOP, score it, and move on. Earlier versions ran the pacing in the clients, and a round could start and end immediately because each client decided on its own when to move on.

Websocket consumers can't be that authority. There is one per connection, it goes away when the player disconnects, and several players connect to the same party. The project already runs Redis for the Channels layer, and the goal is to stay inside the Django Channels ecosystem.

## Decision

The lifecycle runs in `PartyStateMachine`, an `AsyncConsumer` bound to the `party-state-machine` channel through `ChannelNameRouter`. The `channel-master` and `channel-worker` containers run it with `manage.py custom_runworker *`.

- Every `PartyConsumer` connection sends `event_party_started`. The first worker to lock the `Party` row with `SELECT ... FOR UPDATE SKIP LOCKED` (where `started_at IS NULL`) runs the party. The others skip it.
- The winning worker drives the whole party in that one coroutine. Per-party channels act as private queues: `party_players_{id}` for joins, and `party_new_round_{id}` for "end this round now". It broadcasts to the `party_{id}` group.
- After a restart, `CoreConfig.ready()` on the worker with `CHANNELS_WORKER_MASTER=1` sends `event_party_started` with `force_start` for every party that has started but isn't closed.

Celery or a separate game-loop service were not adopted, because either would add infrastructure to a hobby-scale project.

## Consequences

- There is no extra infrastructure: Redis and the Channels worker processes do everything.
- Round timing and ordering are decided in one place, so clients can't get out of step.
- A party takes a whole worker for its entire duration, and a busy worker keeps queuing incoming messages behind it. Concurrent parties are capped by the number of workers, and a STOP can wait for a whole party to end ([#9]).
- Lifecycle state lives in the coroutine's memory apart from a few timestamps. A party's end is only recorded because scoring sets `closed_at` after the last round ([#5]). After a restart the open round's timer starts again from zero, and nothing guarantees that only one worker owns the party ([#10]).
- Everything sent through the channel layer has to be msgpack-serializable, so events carry ids, not model instances ([#4]).
- Waiting-room presence is read from `channels_redis`'s private group keys, and it counts connections instead of players ([#15]).

These problems are why [0004](0004-persisted-event-driven-party-state-machine.md) proposes replacing this design.

[#4]: https://github.com/DanielGnzlzVll/AACX/issues/4
[#5]: https://github.com/DanielGnzlzVll/AACX/issues/5
[#9]: https://github.com/DanielGnzlzVll/AACX/issues/9
[#10]: https://github.com/DanielGnzlzVll/AACX/issues/10
[#15]: https://github.com/DanielGnzlzVll/AACX/issues/15
