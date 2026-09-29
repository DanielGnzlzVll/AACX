# AACX

Welcome to AACX, a playful exploration of various technologies and concepts through a simple yet engaging video game.

AACX delves into the following technologies and concepts:

1. Django channels
1. HTMX
1. Hypermedia-driven UI: the server sends HTML, over HTTP and websockets
1. View Transitions.


## Setup

Let's get started with setting up and running the application.

1. **Requirements**:
    Ensure you have the following dependencies installed:
   - docker

2. **Start the project**:
   - Open a terminal in the root directory of your project.
   - Execute the following command to initiate the project setup:
        
        ```bash
        docker compose up
        ```
   - If `db` fails with "database files are incompatible with server", the `pgdata` volume was created by a different PostgreSQL major version. Reset it with `docker compose down -v`.

3. **Populate the Database**:
    The database is automatically created and populated with necessary data.

## Getting Started

To embark on your gaming journey, simply open your browser and navigate to http://localhost:8000/home/ (nothing is served at `/`). You'll be asked for a nickname: pick any free one and you're in. The nickname stays tied to that browser, see [ADR 0003](docs/adr/0003-passwordless-nickname-login.md).

### home page

Explore existing parties or create your own.

<img src="https://github.com/DanielGnzlzVll/AACX/assets/22230373/3f04b56b-15c4-45b8-b185-e11eadf9df6f" alt="image" width=650 height="auto" />

### Create a new party

Personalize your party by giving it a unique name or adjusting its settings to your liking.


<img src="https://github.com/DanielGnzlzVll/AACX/assets/22230373/3e33e576-d66a-4a97-a82d-8dfe72beda9a" alt="img" width=650 height="auto" />

Upon successful creation, a confirmation message will appear

<img src="https://github.com/DanielGnzlzVll/AACX/assets/22230373/c78bef61-cee1-4c33-be93-d14d70463883" alt="image" width=650 height="auto" />



### Join a party

Simply click on a party to join. Depending on the settings, you may need to wait for other players to join before starting.

However you may have to wait until all players joins the party(you can change the minium number of players when creating the party)

<img src="https://github.com/DanielGnzlzVll/AACX/assets/22230373/13dd942f-9700-4d96-ac83-aa34c5625e38" alt="img" width=650 height="auto" />


### Gameplay

Once the party begins, your task is to provide answers for each round, starting with the designated letter. Track your progress, view other players' scores, and their answers too.

<img src="https://github.com/DanielGnzlzVll/AACX/assets/22230373/d3182bc3-93fb-413f-838e-b28fabd00464" alt="img" width=650 height="auto" />

Incorrect answers will be highlighted for your attention.

<img src="https://github.com/DanielGnzlzVll/AACX/assets/22230373/57ac5690-41a0-47ad-b9fc-27b1e46d0af0" alt="img" width=650 height="auto" />

When a round ends, either due to timeout or player intervention, all answers will be revealed, and scores updated.

<img src="https://github.com/DanielGnzlzVll/AACX/assets/22230373/c9541cf1-caf9-4c2f-af96-89c5d904c2ca" alt="img" width=650 height="auto" />

## Testing

The test suite uses pytest, pytest-django and the Channels testing tools, and runs against Postgres.

Run it inside Docker:

```bash
docker compose run --rm server pytest
```

Or locally with Python 3.11, pointing the tests at any Postgres and Redis instances:

```bash
pip install -r requirements-dev.txt
docker run -d --name aacx-test-db -p 5432:5432 \
    -e POSTGRES_USER=django_user -e POSTGRES_PASSWORD=django_password postgres:16
docker run -d --name aacx-test-redis -p 6379:6379 redis:7
POSTGRES_HOST=localhost POSTGRES_PORT=5432 REDIS_HOST=localhost REDIS_PORT=6379 pytest
```

Tests use `asacx/settings_test.py`, which swaps Redis for an in-memory channel layer and cache. Party leases still need a real Redis.

## Architecture

- [Architecture overview](docs/architecture.md): runtime topology, message flow between Daphne, `PartyConsumer`, `PartyStateMachine`, Redis and Postgres, the party lifecycle, the data model and the frontend model.
- [Architecture decision records](docs/adr/README.md).

## Project decisions:
In developing AACX, several key decisions were made. The ones that shape the architecture are recorded as [ADRs](docs/adr/README.md):

1. Utilization of Django: Leveraging Django's MVC architecture and template system for maximal synergy with HTMX.
1. Integration of Django Channels: Exploring real-time operations via websockets in Python, diverging from the conventional Node.js or Golang implementations.
1. Adoption of HTMX: Experimenting with HTMX to create richly interactive pages with minimal JavaScript.
1. Incorporation of View Transitions: Embracing Google Chrome's new view transitions as a potential future standard for web pages, acknowledging the evolving landscape of web technologies.
1. While testing is typically a vital aspect of software development, it was approached differently in this project. Despite acknowledging its importance and usually prioritizing testing before code implementation, the nature of this project involved numerous new technologies and uncertain decisions. Therefore, testing was not conducted in the traditional sense. Instead, the development process was more of an experimental trial-and-error endeavor. Each decision was made cautiously, with constant exploration and adaptation to the evolving landscape of technologies.