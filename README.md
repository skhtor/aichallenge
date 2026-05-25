# AI Ants Challenge

A competitive AI programming challenge where bots play the [Ants](http://aichallenge.org/) game. Teams upload bots, the server runs matches automatically, and a live leaderboard tracks ELO ratings.

## Quick Start

```bash
docker-compose up --build
```

Server available at **http://localhost:5001**

See [SERVER_INSTRUCTIONS.md](SERVER_INSTRUCTIONS.md) for full usage details (uploading bots, API, supported languages).

## Project Structure

| Directory | Description |
|-----------|-------------|
| `server/` | FastAPI web server, match worker, database layer |
| `ants/` | Game engine, starter packages, maps, visualizer |

## Development

The Makefile wraps common docker-compose commands:

```bash
make up        # Start services (detached)
make build     # Rebuild and start
make logs      # Tail logs
make restart   # Restart web + worker
make down      # Stop everything
make db-shell  # psql into the database
```

## Installation

See [INSTALL.md](INSTALL.md) for Docker and local setup instructions.

## CI/CD

Pushing to the `epsilon` branch builds and publishes a Docker image to `ghcr.io` via GitHub Actions.
