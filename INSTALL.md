# Installation

## Docker (Recommended)

Prerequisites: Docker and Docker Compose.

```bash
# Clone the repo
git clone <repo-url> && cd aichallenge

# Copy environment file
cp .env.example .env

# Start all services (PostgreSQL, web server, worker)
docker-compose up --build
```

The server runs at **http://localhost:5001**. Source changes in `server/` and `ants/` are mounted live — restart the container to pick them up.

### Services

| Service | Description |
|---------|-------------|
| `db` | PostgreSQL 16 (port 5432) |
| `web` | FastAPI server (port 5001, 4 workers) |
| `worker` | Match runner (depends on db + web) |

### Useful commands

```bash
make up        # Start detached
make logs      # Tail logs
make db-shell  # psql into the database
make down      # Stop and remove containers
```

## Local (No Docker)

Prerequisites: Python 3.10+, PostgreSQL, g++. Optional: JDK, Go, Node.js, Ruby (for those bot languages).

```bash
cd server

# Virtual environment
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Set database URL (or start a local PostgreSQL instance)
export DATABASE_URL=postgresql://ants:ants@localhost:5432/ants

# Start the server
uvicorn app:app --host 0.0.0.0 --port 5001

# In a separate terminal, start the worker
cd server && source .venv/bin/activate
export DATABASE_URL=postgresql://ants:ants@localhost:5432/ants
python -u worker.py
```

## Production

Production runs on Kubernetes. The CI/CD pipeline (GitHub Actions on the `epsilon` branch) builds and pushes the Docker image to `ghcr.io`, which is then deployed to the cluster.

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL connection string | (required) |
| `SEED_SAMPLE_BOTS` | Seed sample bots on first start | `false` |
| `ADMIN_SECRET` | Secret for admin API endpoints | (empty) |
