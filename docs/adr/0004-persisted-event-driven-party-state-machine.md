# 0004. Persisted, event-driven party state machine

## Status

Accepted, partly implemented. Tracked in [#1]. Rounds close through a conditional update and STOP events carry ids ([#4]), the end of a party is persisted ([#5]), each party runs in its own task ([#9]), waiting-room presence counts players from the DB ([#15]), each `PartyConsumer` renders its player's fragments ([#18]), clients count down from `deadline_at` ([#27]), each party has a single owner that a reconciler on every worker resumes after a restart ([#10], [#82]), and rounds persist their `number` and `closed_reason` ([#1]). Still to do: a persisted `status` that includes `ABANDONED` ([#83]), and the lease as the only ownership mechanism in place of the waiting-room claim plus the lease ([#89]). Supersedes [0002](0002-party-state-machine-as-channels-worker.md) once implemented.

## Context

[0002](0002-party-state-machine-as-channels-worker.md) runs a whole party as one long coroutine, `PartyStateMachine.event_party_started`, inside a Channels worker. The only persisted state is a few nullable timestamps. Several confirmed bugs come from that design:

- One party occupies a whole worker for its entire duration ([#9]).
- Events carried Django model instances, which the Redis layer can't serialize ([#4]).
- The end of a party was never persisted ([#5]).
- Resuming after a restart replayed the whole game, and nothing gave a party a single owner ([#10]).
- Waiting-room presence relied on `channels_redis` private internals and counted connections instead of players ([#15]).

## Decision

1. **Explicit persisted state.** `Party.status` goes `WAITING → IN_PROGRESS → FINISHED`, and can also be `ABANDONED`. `PartyRound` gains `number`, `deadline_at` and `closed_reason` (`timeout` or `stop`).
2. **Idempotent, DB-conditional transitions.** For example: `UPDATE ... SET closed_at = now() WHERE id = %s AND closed_at IS NULL`. Only the caller whose update wins acts on the transition, so duplicate STOPs, retries and concurrent workers are harmless.
3. **Events carry ids only** (`party_id`, `round_id`). They serialize, and a stale event for an old round can be recognized and ignored.
4. **Non-blocking runner.** Each party is driven by its own `asyncio.Task`, and consumer handlers return immediately, so one worker can run many parties.
5. **Single owner per party.** The runner holds and renews a Redis lease (`python-redis-lock` is already a dependency). A reconciler runs periodically on every worker, with a random initial offset, and resumes each `IN_PROGRESS` party that has no live lease. It works from DB state: the current round, and the time left until `deadline_at`. The lease makes duplicate resumptions from several workers harmless, so no worker is a singleton that parties depend on.
6. **Shared and per-player rendering are separate.** Group broadcasts carry only shared state (letter, scores, status). Per-player fragments are rendered by that player's `PartyConsumer` ([#18]).

The target lifecycle is drawn in [`architecture.md`](../architecture.md#target).

### Alternatives considered

- **Celery with ETA tasks for round deadlines.** It adds a broker and a second kind of worker for little gain.
- **A separate game-loop service.** Too much for this scale.

## Consequences

- Any worker can crash or restart at any moment, and stay down. The DB is the source of truth, and the reconciler on the remaining workers resumes each party exactly where it stopped.
- The number of concurrent parties is no longer tied to the number of workers.
- Duplicate or late events are safe by construction, and tests can target each transition on its own.
- Adding a status and round fields requires a migration, including a backfill for existing parties.
- Lease renewal and the reconciler are new moving parts. They need tests for expiry and takeover.
- Clients can show a countdown from `deadline_at` ([#27]).

[#1]: https://github.com/DanielGnzlzVll/AACX/issues/1
[#4]: https://github.com/DanielGnzlzVll/AACX/issues/4
[#5]: https://github.com/DanielGnzlzVll/AACX/issues/5
[#9]: https://github.com/DanielGnzlzVll/AACX/issues/9
[#10]: https://github.com/DanielGnzlzVll/AACX/issues/10
[#15]: https://github.com/DanielGnzlzVll/AACX/issues/15
[#18]: https://github.com/DanielGnzlzVll/AACX/issues/18
[#27]: https://github.com/DanielGnzlzVll/AACX/issues/27
[#82]: https://github.com/DanielGnzlzVll/AACX/issues/82
[#83]: https://github.com/DanielGnzlzVll/AACX/issues/83
[#89]: https://github.com/DanielGnzlzVll/AACX/issues/89
