# 0001. Server-rendered HTML with HTMX, including over websockets

## Status

Accepted

## Context

AACX is a real-time multiplayer game: players see each other join, answer at the same time, and all see a round end at the same moment. The project also exists to try out Django's template system, Django Channels and HTMX together, keeping client-side JavaScript to a minimum.

The usual approach would be a JavaScript single-page app that talks to a JSON API and a JSON websocket protocol. That means two rendering layers, a client-side state store, and a message schema kept in sync between Python and JavaScript.

## Decision

The server renders all UI as HTML, both for HTTP responses and for websocket messages.

- **HTTP**: views render Django templates. `HTMXPartialMixin` gives HTMX requests `base_partial.html` and normal requests the full `base.html` layout, through the `base_template` context variable, so each URL works as a full page load and as a fragment. Navigation uses `hx-get` with `hx-push-url` and the View Transitions API.
- **Websocket**: the party page opens its socket with the htmx `ws` extension. The browser sends form fields as JSON with `ws-send`. The server sends back HTML fragments, and the extension swaps each top-level element into the element with the same `id` (an out-of-band swap).
- **JavaScript** is limited to htmx, its extensions, and small inline scripts where htmx can't do the job.

## Consequences

- There is one rendering layer and no client state: the page always shows the last HTML the server sent.
- Element ids are the contract between templates and consumers. Renaming an id breaks a swap without any error, so the swap targets are listed in [`architecture.md`](../architecture.md#6-frontend-model).
- A group broadcast is rendered once and sent to everyone, so it can only contain state that all players share. Anything specific to one player has to be rendered by that player's `PartyConsumer`. Mixing the two emptied each player's past-answers panel at the start of every round ([#18]).
- A swap must never replace an input the player may be typing in: it loses focus and the cursor, and drops whatever was typed while the message was in flight. Replies to autosave only swap small per-field status elements ([#20]).
- Websocket messages are markup, not a versioned API. Only this app's own templates can use them.
- The app follows hypermedia principles, since the server sends HTML with the next actions in it. Page GETs are read-only; game state only changes through the websocket and the state machine.

[#18]: https://github.com/DanielGnzlzVll/AACX/issues/18
[#20]: https://github.com/DanielGnzlzVll/AACX/issues/20
