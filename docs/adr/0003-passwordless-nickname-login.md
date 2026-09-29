# 0003. Passwordless nickname login

## Status

Accepted

## Context

AACX is a casual party game. Asking players to register with an email and password before a quick match would put most of them off. Players still need a stable identity: answers, scores and party membership belong to a user, and the websocket needs to know who is sending answers.

An earlier version logged anyone in as any existing account by username alone, superusers included, which gave full Django admin access from a username ([#2]).

## Decision

Players log in at `/login/` with only a nickname. Each player is a normal `django.contrib.auth` `User`, and Django sessions authenticate both HTTP requests and websockets (`AuthMiddlewareStack`).

- `LoginForm` validates the nickname: required, stripped, 3 to 30 characters, ASCII letters, digits, `.`, `_` and `-`.
- An unknown nickname creates a `User` with an unusable password, and the player is logged in.
- A known nickname is looked up case-insensitively. It is accepted only when the user's id is in the browser's signed `aacx_nickname_claim` cookie. Each login writes that cookie with the ids of the browser's 10 most recently used nicknames. It is `HttpOnly`, uses `SameSite=Lax`, is `Secure` over HTTPS, and lasts a year. The nickname belongs to the browser that claimed it first.
- Staff, superuser, inactive and password-protected accounts are always rejected at `/login/`. They log in through `/admin/login/`.
- After login, the player goes to the `next` URL if it's on the same host, and to `/home/` otherwise. `/logout/` accepts only POST (`core.views.Logout`).

## Consequences

- A new player needs one field to start playing.
- Players can't impersonate each other, and admin accounts can't be reached through the game's login.
- There is no account recovery. A player who clears cookies or switches browser can't use their nickname again and has to pick a new one. A browser remembers at most 10 nicknames. Logging in with an 11th drops the oldest from the cookie, and that nickname can no longer be used from that browser.
- Rotating `SECRET_KEY` invalidates every claim cookie, so all nicknames become unclaimable.
- Authenticating a websocket isn't the same as authorizing it. `PartyConsumer` doesn't check that the user is logged in or belongs to the party ([#16]).

[#2]: https://github.com/DanielGnzlzVll/AACX/issues/2
[#16]: https://github.com/DanielGnzlzVll/AACX/issues/16
