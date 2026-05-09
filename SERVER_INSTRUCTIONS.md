# AI Ants Challenge Server - Setup & Usage

## Quick Start (Docker - Recommended)

```bash
cd /path/to/aichallenge
docker-compose up --build
```

The server will be available at **http://localhost:5000**

This starts two containers:
- **web** — FastAPI web server (leaderboard, uploads, replays)
- **worker** — Runs matches automatically between uploaded bots

## Quick Start (Local - No Docker)

```bash
cd /path/to/aichallenge/server

# Create virtual environment (one-time)
python3 -m venv .venv
source .venv/bin/activate
pip install fastapi uvicorn[standard] python-multipart jinja2

# Start the server
uvicorn app:app --host 0.0.0.0 --port 5000
```

In a separate terminal, start the worker:
```bash
cd /path/to/aichallenge/server
source .venv/bin/activate
python -u worker.py
```

Server runs at **http://localhost:5000**

### Requirements for local mode
- Python 3.10+
- g++ (for C++ bot compilation)
- Java JDK (for Java bots)
- Node.js, Ruby, Go (optional, for those languages)

## Uploading a Bot

### Via Web UI
1. Go to http://localhost:5000
2. Register a team (enter a team name, save the token)
3. Fill in the upload form: team token, bot name, and zip file
4. The server validates the bot with a 50-turn test match before accepting

### Via API (curl)

Register a team:
```bash
curl -X POST http://localhost:5000/api/register -d "name=MyTeam"
# Returns: {"team": "MyTeam", "token": "your-token-here"}
```

Upload a bot:
```bash
curl -X POST http://localhost:5000/api/upload \
  -H "Authorization: Bearer your-token-here" \
  -F "bot_name=MyBot" \
  -F "file=@myBot_cpp_upload.zip"
```

### Supported Languages
| Language   | Entry Point    | Compiled |
|------------|---------------|----------|
| Python     | MyBot.py      | No       |
| C++        | MyBot.cc/cpp  | Yes (g++ -O2) |
| Java       | MyBot.java    | Yes (javac) |
| Go         | MyBot.go      | Yes (go build) |
| JavaScript | MyBot.js      | No       |
| Ruby       | MyBot.rb      | No       |

### What to upload
- A `.zip` file containing your source files (no subdirectories needed)
- For C++: include all `.cc`, `.cpp`, and `.h` files plus a `Makefile` (optional — server compiles with `g++ -O2 -o MyBot *.cc`)

## How It Works
- The **worker** continuously picks 2-4 active bots, runs a match on a random map, and updates ELO ratings
- Matches are played using the ants game engine (`ants/playgame.py`)
- Replays are viewable in-browser via the built-in visualizer
- Sample bots (HunterBot, LeftyBot, GreedyBot, RandomBot) are seeded automatically

## Stopping the Server

Docker:
```bash
docker-compose down
```

Local: Ctrl+C both the uvicorn and worker processes.

## Data Persistence
- **Docker**: Data is stored in named volumes (`bots_data`, `replays_data`, `db_data`)
- **Local**: Data is in `server/bots/`, `server/replays/`, and `server/data/` (SQLite DB)
