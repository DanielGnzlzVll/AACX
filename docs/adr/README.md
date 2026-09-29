# Architecture decision records

Each ADR records one significant design decision: the context, the decision, and its consequences. They follow Michael Nygard's [format](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions). The system as a whole is described in [`docs/architecture.md`](../architecture.md).

| ADR | Title | Status |
|---|---|---|
| [0001](0001-server-rendered-html-with-htmx-over-websockets.md) | Server-rendered HTML with HTMX, including over websockets | Accepted |
| [0002](0002-party-state-machine-as-channels-worker.md) | Run the party lifecycle as a Channels worker | Accepted, to be superseded by 0004 |
| [0003](0003-passwordless-nickname-login.md) | Passwordless nickname login | Accepted |
| [0004](0004-persisted-event-driven-party-state-machine.md) | Persisted, event-driven party state machine | Accepted, partly implemented |
| [0005](0005-validate-answers-with-word-lists.md) | Validate answers with word lists, not a local model | Accepted |

## Writing a new ADR

1. Copy the template below into `NNNN-short-title.md`, using the next free number.
2. Open it as **Proposed** in the pull request that discusses it. Set it to **Accepted** once the decision is agreed.
3. Don't rewrite accepted ADRs. When a decision changes, add a new ADR and mark the old one **Superseded by NNNN**.
4. Add it to the table above.

```markdown
# NNNN. Title

## Status

Proposed

## Context

What forces are at play, and why a decision is needed.

## Decision

What we do.

## Consequences

What becomes easier or harder, including the trade-offs we accept.
```
