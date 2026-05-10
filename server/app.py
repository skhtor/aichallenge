"""AI Ants Challenge - Web Server (FastAPI + uvicorn)."""
import hmac
import json
import secrets
import shutil
import time
import zipfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

MELB_TZ = ZoneInfo("Australia/Melbourne")

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from jinja2 import Environment, FileSystemLoader

from db import get_db, init_db
from languages import detect_language, compile_bot, get_starter_files_dir, LANGUAGES

BASE_DIR = Path(__file__).parent
BOTS_DIR = BASE_DIR / "bots"
REPLAYS_DIR = BASE_DIR / "replays"
ANTS_DIR = BASE_DIR.parent / "ants"

MAX_UPLOAD_SIZE = 5 * 1024 * 1024  # 5MB
MAX_ZIP_FILES = 50  # max files in a zip
MAX_ZIP_EXTRACTED_SIZE = 20 * 1024 * 1024  # 20MB total extracted
MAX_BOT_NAME_LENGTH = 32
MAX_BOTS_PER_TEAM = 10

# Simple in-memory rate limiters with bounded size
_RATE_LIMIT_MAX_ENTRIES = 10000


class RateLimiter:
    """Bounded rate limiter that evicts oldest entries when full."""
    def __init__(self, cooldown: float, max_entries: int = _RATE_LIMIT_MAX_ENTRIES):
        self._cooldown = cooldown
        self._max_entries = max_entries
        self._times: dict[str, float] = {}

    def is_limited(self, key: str) -> bool:
        now = time.time()
        last = self._times.get(key, 0)
        if now - last < self._cooldown:
            return True
        self._times[key] = now
        if len(self._times) > self._max_entries:
            # Evict oldest quarter
            sorted_keys = sorted(self._times, key=self._times.get)
            for k in sorted_keys[:self._max_entries // 4]:
                del self._times[k]
        return False


_upload_limiter = RateLimiter(cooldown=30)
_register_limiter = RateLimiter(cooldown=10)
_test_limiter = RateLimiter(cooldown=60)


def _verify_token(token: str):
    """Verify a team token using constant-time comparison. Returns team row or None."""
    db = get_db()
    # Fetch all tokens and compare in constant time to prevent timing attacks
    teams = db.execute("SELECT id, name, token FROM teams").fetchall()
    for team in teams:
        if hmac.compare_digest(team["token"], token):
            db.close()
            return {"id": team["id"], "name": team["name"]}
    db.close()
    return None

BOTS_DIR.mkdir(exist_ok=True)
REPLAYS_DIR.mkdir(exist_ok=True)

app = FastAPI(title="AI Ants Challenge")
app.mount("/visualizer", StaticFiles(directory=str(BASE_DIR / "static")), name="visualizer")
jinja_env = Environment(loader=FileSystemLoader(str(BASE_DIR / "templates")), autoescape=True)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'"
    return response


@app.on_event("startup")
def startup():
    init_db()
    _seed_sample_bots()


def _seed_sample_bots():
    """Register sample bots if they exist on disk but not in DB."""
    db = get_db()
    team = db.execute("SELECT id FROM teams WHERE name = 'Sample'").fetchone()
    if not team:
        token = "sample-" + secrets.token_hex(8)
        db.execute("INSERT INTO teams (name, token, created_at) VALUES (?, ?, ?)",
                   ("Sample", token, datetime.now(MELB_TZ).isoformat()))
        db.commit()
        team = db.execute("SELECT id FROM teams WHERE name = 'Sample'").fetchone()
    team_id = team["id"]

    for bot_dir in BOTS_DIR.iterdir():
        if bot_dir.is_dir() and not (bot_dir / "run.sh").exists():
            language = detect_language(bot_dir)
            if language:
                config = LANGUAGES[language]
                run_sh = bot_dir / "run.sh"
                run_sh.write_text(f"#!/bin/sh\ncd {bot_dir}\n{config['run']}\n")
                run_sh.chmod(0o755)

        if bot_dir.is_dir() and detect_language(bot_dir):
            name = bot_dir.name
            lang = detect_language(bot_dir)
            existing = db.execute("SELECT id FROM bots WHERE name = ?", (name,)).fetchone()
            if not existing:
                db.execute("INSERT INTO bots (name, team_id, language, active_version) VALUES (?, ?, ?, 1)",
                           (name, team_id, lang))
                db.commit()
                bot = db.execute("SELECT id FROM bots WHERE name = ?", (name,)).fetchone()
                db.execute("INSERT INTO bot_versions (bot_id, version, uploaded_at) VALUES (?, 1, ?)",
                           (bot["id"], datetime.now(MELB_TZ).isoformat()))
    db.commit()
    db.close()


# --- Pages ---

@app.get("/", response_class=HTMLResponse)
def index():
    db = get_db()
    bots = [dict(r) for r in db.execute("""
        SELECT b.*, t.name as team_name FROM bots b
        JOIN teams t ON b.team_id = t.id ORDER BY b.elo DESC
    """).fetchall()]
    recent_matches = db.execute("""
        SELECT m.id, m.played_at, m.turns, m.map_file, m.replay_file
        FROM matches m ORDER BY m.id DESC LIMIT 20
    """).fetchall()

    matches_with_players = []
    for match in recent_matches:
        players = [dict(r) for r in db.execute("""
            SELECT mp.*, b.name as bot_name
            FROM match_players mp JOIN bots b ON mp.bot_id = b.id
            WHERE mp.match_id = ? ORDER BY mp.player_index
        """, (match["id"],)).fetchall()]
        matches_with_players.append({"match": dict(match), "players": players})
    db.close()
    html = jinja_env.get_template("index.html").render(bots=bots, matches=matches_with_players)
    return HTMLResponse(html)


@app.get("/replay/{match_id}", response_class=HTMLResponse)
def view_replay(match_id: int):
    db = get_db()
    match = db.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()
    db.close()
    if not match or not match["replay_file"]:
        raise HTTPException(404, "Replay not found")
    return HTMLResponse(jinja_env.get_template("replay.html").render(match_id=match_id))


@app.get("/replay_data/{match_id}")
def replay_data(match_id: int):
    db = get_db()
    match = db.execute("SELECT replay_file FROM matches WHERE id = ?", (match_id,)).fetchone()
    db.close()
    if not match or not match["replay_file"]:
        raise HTTPException(404, "Replay not found")
    replay_path = (REPLAYS_DIR / match["replay_file"]).resolve()
    if not str(replay_path).startswith(str(REPLAYS_DIR.resolve())):
        raise HTTPException(403, "Invalid replay path")
    if not replay_path.exists():
        raise HTTPException(404, "Replay file missing")
    return FileResponse(replay_path, media_type="application/json")


@app.get("/bot/{bot_name}", response_class=HTMLResponse)
def bot_profile(bot_name: str):
    db = get_db()
    bot = db.execute("""
        SELECT b.*, t.name as team_name FROM bots b
        JOIN teams t ON b.team_id = t.id WHERE b.name = ?
    """, (bot_name,)).fetchone()
    if not bot:
        raise HTTPException(404, "Bot not found")
    bot = dict(bot)
    versions = [dict(r) for r in db.execute(
        "SELECT * FROM bot_versions WHERE bot_id = ? ORDER BY version DESC", (bot["id"],)
    ).fetchall()]
    matches = [dict(r) for r in db.execute("""
        SELECT mp.*, m.played_at, m.turns, m.map_file, m.replay_file
        FROM match_players mp JOIN matches m ON mp.match_id = m.id
        WHERE mp.bot_id = ? ORDER BY m.id DESC LIMIT 30
    """, (bot["id"],)).fetchall()]
    db.close()
    html = jinja_env.get_template("bot_profile.html").render(bot=bot, versions=versions, matches=matches)
    return HTMLResponse(html)


# --- API ---

@app.post("/api/register")
def register_team(name: str = Form(...), request: Request = None):
    """Register a new team and get an auth token."""
    client_ip = request.client.host if request and request.client else "unknown"
    if _register_limiter.is_limited(client_ip):
        raise HTTPException(429, "Too many registrations. Try again later.")

    db = get_db()
    existing = db.execute("SELECT id FROM teams WHERE name = ?", (name,)).fetchone()
    if existing:
        db.close()
        raise HTTPException(400, "Team name already taken")
    token = secrets.token_hex(16)
    db.execute("INSERT INTO teams (name, token, created_at) VALUES (?, ?, ?)",
               (name, token, datetime.now(MELB_TZ).isoformat()))
    db.commit()
    db.close()
    return {"team": name, "token": token}


def _extract_upload(bot_dir: Path, content: bytes, filename: str) -> str | None:
    """Extract uploaded file to bot_dir. Returns error message or None."""
    if filename.endswith(".zip"):
        zip_path = bot_dir / "upload.zip"
        zip_path.write_bytes(content)
        with zipfile.ZipFile(zip_path, "r") as zf:
            members = zf.namelist()
            if len(members) > MAX_ZIP_FILES:
                zip_path.unlink()
                return f"Zip contains too many files (max {MAX_ZIP_FILES})"
            total_size = sum(info.file_size for info in zf.infolist())
            if total_size > MAX_ZIP_EXTRACTED_SIZE:
                zip_path.unlink()
                return f"Zip extracted size too large (max {MAX_ZIP_EXTRACTED_SIZE // 1024 // 1024}MB)"
            for member in members:
                member_path = (bot_dir / member).resolve()
                if not str(member_path).startswith(str(bot_dir.resolve())):
                    zip_path.unlink()
                    return "Invalid zip: contains path traversal"
            zf.extractall(bot_dir)
        zip_path.unlink()
    elif any(filename.endswith(ext) for ext in (".py", ".java", ".cc", ".cpp", ".go", ".js", ".rb")):
        # Determine the correct MyBot.* filename
        ext = Path(filename).suffix
        entry_names = {"py": "MyBot.py", "java": "MyBot.java", "cc": "MyBot.cc",
                       "cpp": "MyBot.cpp", "go": "MyBot.go", "js": "MyBot.js", "rb": "MyBot.rb"}
        target = entry_names.get(ext.lstrip("."), filename)
        (bot_dir / target).write_bytes(content)
    else:
        return "Upload .zip or a source file (.py, .java, .cc, .cpp, .go, .js, .rb)"
    return None


def _setup_bot_language(bot_dir: Path) -> tuple[str | None, str]:
    """Detect language, copy starter libs, compile. Returns (language, error)."""
    language = detect_language(bot_dir)
    if not language:
        return None, "Could not detect language. Ensure your entry point is named MyBot.py/java/cc/cpp/go/js/rb"

    # Copy starter library files if not already present
    starter_dir = get_starter_files_dir(language, ANTS_DIR)
    if starter_dir:
        for f in starter_dir.iterdir():
            if f.is_file() and f.name.startswith("MyBot"):
                continue  # Don't overwrite their bot
            if f.is_file() and not (bot_dir / f.name).exists():
                shutil.copy(f, bot_dir / f.name)

    # Compile if needed
    success, err = compile_bot(bot_dir, language)
    if not success:
        return None, f"Compilation failed: {err}"

    return language, ""


@app.post("/api/upload")
async def upload_bot(
    bot_name: str = Form(...),
    file: UploadFile = File(...),
    authorization: str = Header(...),
):
    """Upload a bot. Requires Authorization header with team token."""
    token = authorization.replace("Bearer ", "").strip()

    # Rate limit
    if _upload_limiter.is_limited(token):
        raise HTTPException(429, "Upload too frequent. Wait 30 seconds between uploads.")

    team = _verify_token(token)
    if not team:
        raise HTTPException(401, "Invalid token")

    db = get_db()

    safe_name = "".join(c for c in bot_name if c.isalnum() or c in "-_")[:MAX_BOT_NAME_LENGTH]
    if not safe_name:
        db.close()
        raise HTTPException(400, "Invalid bot name")

    existing = db.execute("SELECT id, team_id, active_version FROM bots WHERE name = ?", (safe_name,)).fetchone()
    if existing and existing["team_id"] != team["id"]:
        db.close()
        raise HTTPException(403, "Bot belongs to another team")
    if not existing:
        bot_count = db.execute("SELECT COUNT(*) as cnt FROM bots WHERE team_id = ?", (team["id"],)).fetchone()["cnt"]
        if bot_count >= MAX_BOTS_PER_TEAM:
            db.close()
            raise HTTPException(400, f"Maximum {MAX_BOTS_PER_TEAM} bots per team")

    bot_dir = BOTS_DIR / safe_name
    if bot_dir.exists():
        shutil.rmtree(bot_dir)
    bot_dir.mkdir()

    content = await file.read()
    if len(content) > MAX_UPLOAD_SIZE:
        shutil.rmtree(bot_dir)
        db.close()
        raise HTTPException(413, f"File too large. Maximum size is {MAX_UPLOAD_SIZE // 1024 // 1024}MB.")
    err = _extract_upload(bot_dir, content, file.filename or "MyBot.py")
    if err:
        shutil.rmtree(bot_dir)
        db.close()
        raise HTTPException(400, err)

    language, err = _setup_bot_language(bot_dir)
    if not language:
        shutil.rmtree(bot_dir)
        db.close()
        raise HTTPException(400, err)

    # Upsert bot + version
    if existing:
        new_version = (existing["active_version"] or 0) + 1
        db.execute("UPDATE bots SET active_version = ?, language = ? WHERE id = ?",
                   (new_version, language, existing["id"]))
        db.execute("INSERT INTO bot_versions (bot_id, version, uploaded_at) VALUES (?, ?, ?)",
                   (existing["id"], new_version, datetime.now(MELB_TZ).isoformat()))
    else:
        db.execute("INSERT INTO bots (name, team_id, language, active_version) VALUES (?, ?, ?, 1)",
                   (safe_name, team["id"], language))
        db.commit()
        bot = db.execute("SELECT id FROM bots WHERE name = ?", (safe_name,)).fetchone()
        db.execute("INSERT INTO bot_versions (bot_id, version, uploaded_at) VALUES (?, 1, ?)",
                   (bot["id"], datetime.now(MELB_TZ).isoformat()))
    db.commit()
    db.close()
    return {"status": "ok", "bot": safe_name, "language": language, "version": new_version if existing else 1}


@app.get("/api/leaderboard")
def api_leaderboard():
    db = get_db()
    bots = db.execute("""
        SELECT b.name, t.name as team, b.elo, b.games_played, b.wins, b.language, b.active
        FROM bots b JOIN teams t ON b.team_id = t.id ORDER BY b.elo DESC
    """).fetchall()
    db.close()
    return [dict(b) for b in bots]


@app.post("/api/bot/{bot_name}/deactivate")
def deactivate_bot(bot_name: str, authorization: str = Header(...)):
    """Deactivate a bot (remove from matchmaking). Requires team token."""
    token = authorization.replace("Bearer ", "").strip()
    team = _verify_token(token)
    if not team:
        raise HTTPException(401, "Invalid token")
    db = get_db()
    bot = db.execute("SELECT id, team_id FROM bots WHERE name = ?", (bot_name,)).fetchone()
    if not bot:
        db.close()
        raise HTTPException(404, "Bot not found")
    if bot["team_id"] != team["id"]:
        db.close()
        raise HTTPException(403, "Bot belongs to another team")
    db.execute("UPDATE bots SET active = 0 WHERE id = ?", (bot["id"],))
    db.commit()
    db.close()
    return {"status": "ok", "bot": bot_name, "active": False}


@app.post("/api/bot/{bot_name}/activate")
def activate_bot(bot_name: str, authorization: str = Header(...)):
    """Reactivate a bot for matchmaking. Requires team token."""
    token = authorization.replace("Bearer ", "").strip()
    team = _verify_token(token)
    if not team:
        raise HTTPException(401, "Invalid token")
    db = get_db()
    bot = db.execute("SELECT id, team_id FROM bots WHERE name = ?", (bot_name,)).fetchone()
    if not bot:
        db.close()
        raise HTTPException(404, "Bot not found")
    if bot["team_id"] != team["id"]:
        db.close()
        raise HTTPException(403, "Bot belongs to another team")
    db.execute("UPDATE bots SET active = 1 WHERE id = ?", (bot["id"],))
    db.commit()
    db.close()
    return {"status": "ok", "bot": bot_name, "active": True}


@app.delete("/api/bot/{bot_name}")
def delete_bot(bot_name: str, authorization: str = Header(...)):
    """Delete a bot entirely. Requires team token."""
    token = authorization.replace("Bearer ", "").strip()
    team = _verify_token(token)
    if not team:
        raise HTTPException(401, "Invalid token")
    db = get_db()
    bot = db.execute("SELECT id, team_id FROM bots WHERE name = ?", (bot_name,)).fetchone()
    if not bot:
        db.close()
        raise HTTPException(404, "Bot not found")
    if bot["team_id"] != team["id"]:
        db.close()
        raise HTTPException(403, "Bot belongs to another team")
    db.execute("DELETE FROM elo_history WHERE bot_id = ?", (bot["id"],))
    db.execute("DELETE FROM match_players WHERE bot_id = ?", (bot["id"],))
    db.execute("DELETE FROM bot_versions WHERE bot_id = ?", (bot["id"],))
    db.execute("DELETE FROM bots WHERE id = ?", (bot["id"],))
    db.commit()
    db.close()
    bot_dir = BOTS_DIR / bot_name
    if bot_dir.exists():
        shutil.rmtree(bot_dir)
    return {"status": "ok", "bot": bot_name, "deleted": True}


@app.get("/api/matches")
def api_matches():
    db = get_db()
    matches = db.execute("SELECT * FROM matches ORDER BY id DESC LIMIT 50").fetchall()
    db.close()
    return [dict(m) for m in matches]


@app.get("/api/elo_history/{bot_name}")
def api_elo_history(bot_name: str):
    db = get_db()
    bot = db.execute("SELECT id FROM bots WHERE name = ?", (bot_name,)).fetchone()
    if not bot:
        db.close()
        raise HTTPException(404, "Bot not found")
    history = db.execute(
        "SELECT elo, recorded_at FROM elo_history WHERE bot_id = ? ORDER BY id DESC LIMIT 50",
        (bot["id"],)
    ).fetchall()
    db.close()
    return [dict(h) for h in reversed(history)]


@app.get("/api/head_to_head/{bot_a}/{bot_b}")
def api_head_to_head(bot_a: str, bot_b: str):
    """Get head-to-head stats between two bots."""
    db = get_db()
    a = db.execute("SELECT id FROM bots WHERE name = ?", (bot_a,)).fetchone()
    b = db.execute("SELECT id FROM bots WHERE name = ?", (bot_b,)).fetchone()
    if not a or not b:
        db.close()
        raise HTTPException(404, "Bot not found")
    # Find matches where both bots played
    rows = db.execute("""
        SELECT ma.match_id, ma.score as score_a, mb.score as score_b
        FROM match_players ma
        JOIN match_players mb ON ma.match_id = mb.match_id
        WHERE ma.bot_id = ? AND mb.bot_id = ?
    """, (a["id"], b["id"])).fetchall()
    db.close()
    wins_a = sum(1 for r in rows if r["score_a"] > r["score_b"])
    wins_b = sum(1 for r in rows if r["score_b"] > r["score_a"])
    draws = sum(1 for r in rows if r["score_a"] == r["score_b"])
    return {"bot_a": bot_a, "bot_b": bot_b, "matches": len(rows),
            "wins_a": wins_a, "wins_b": wins_b, "draws": draws}


@app.post("/api/test")
async def test_match(
    bot_name: str = Form(...),
    file: UploadFile = File(...),
    request: Request = None,
):
    """Run a test match against sample bots without affecting ELO. Returns result."""
    import subprocess
    import tempfile
    import random

    # Rate limit by IP (reuse register rate limiter with longer cooldown)
    client_ip = request.client.host if request and request.client else "unknown"
    if _test_limiter.is_limited(client_ip):
        raise HTTPException(429, "Test matches limited to once per minute.")

    safe_name = "".join(c for c in bot_name if c.isalnum() or c in "-_") or "TestBot"

    # Save to temp dir
    tmp_dir = Path(tempfile.mkdtemp())
    bot_dir = tmp_dir / safe_name
    bot_dir.mkdir()

    content = await file.read()
    if len(content) > MAX_UPLOAD_SIZE:
        raise HTTPException(413, f"File too large. Maximum size is {MAX_UPLOAD_SIZE // 1024 // 1024}MB.")
    filename = file.filename or "MyBot.py"
    if filename.endswith(".zip"):
        zip_path = bot_dir / "upload.zip"
        zip_path.write_bytes(content)
        with zipfile.ZipFile(zip_path, "r") as zf:
            for member in zf.namelist():
                member_path = (bot_dir / member).resolve()
                if not str(member_path).startswith(str(bot_dir.resolve())):
                    shutil.rmtree(tmp_dir)
                    raise HTTPException(400, "Invalid zip: contains path traversal")
            zf.extractall(bot_dir)
        zip_path.unlink()
    elif filename.endswith(".py"):
        (bot_dir / "MyBot.py").write_bytes(content)
    else:
        shutil.rmtree(tmp_dir)
        raise HTTPException(400, "Upload .py or .zip")

    if not (bot_dir / "MyBot.py").exists():
        shutil.rmtree(tmp_dir)
        raise HTTPException(400, "No MyBot.py found")

    # Copy ants library
    if not (bot_dir / "ants.py").exists():
        shutil.copy(ANTS_DIR / "dist" / "sample_bots" / "python" / "ants.py", bot_dir / "ants.py")

    # Pick 3 sample bots as opponents
    sample_bots = ["HunterBot", "LeftyBot", "GreedyBot"]
    opponents = [f"python3 {BOTS_DIR / name / 'MyBot.py'}" for name in sample_bots]

    # Pick a small map for fast test
    maps_dir = ANTS_DIR / "maps" / "maze"
    test_map = random.choice(list(maps_dir.glob("*p04*.map")))

    log_dir = tmp_dir / "logs"
    log_dir.mkdir()

    cmd = [
        "python3", str(ANTS_DIR / "playgame.py"),
        "--player_seed", str(random.randint(0, 99999)),
        "--end_wait=0.25",
        "--turns", "200",
        "--turntime", "1000",
        "--loadtime", "3000",
        "--map_file", str(test_map),
        "--log_dir", str(log_dir),
        "-R",
        f"python3 {bot_dir / 'MyBot.py'}",
    ] + opponents

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, cwd=str(ANTS_DIR))
    except subprocess.TimeoutExpired:
        shutil.rmtree(tmp_dir)
        return {"status": "error", "message": "Test match timed out"}

    replay_files = list(log_dir.glob("*.replay"))
    if not replay_files:
        shutil.rmtree(tmp_dir)
        return {"status": "error", "message": f"Bot failed to run: {result.stderr[:300]}"}

    with open(replay_files[0]) as f:
        replay_data = json.load(f)

    shutil.rmtree(tmp_dir)

    if "error" in replay_data:
        return {"status": "error", "message": replay_data["error"][:300]}

    names = [safe_name] + sample_bots
    return {
        "status": "ok",
        "players": names,
        "scores": replay_data["score"],
        "game_status": replay_data["status"],
        "turns": replay_data["game_length"],
    }


@app.post("/upload", response_class=HTMLResponse)
async def web_upload(
    team_token: str = Form(...),
    bot_name: str = Form(...),
    file: UploadFile = File(...),
):
    """Web form upload — detects language, compiles, validates with test match."""
    import subprocess
    import tempfile
    import random

    # Rate limit
    if _upload_limiter.is_limited(team_token):
        return HTMLResponse("<h2 style='color:red;'>Upload too frequent. Wait 30 seconds.</h2><a href='/'>Back</a>", status_code=429)

    team = _verify_token(team_token)
    if not team:
        return HTMLResponse("<h2 style='color:red;'>Invalid token</h2><a href='/'>Back</a>", status_code=401)

    db = get_db()
    safe_name = "".join(c for c in bot_name if c.isalnum() or c in "-_")[:MAX_BOT_NAME_LENGTH]
    if not safe_name:
        db.close()
        return HTMLResponse("<h2 style='color:red;'>Invalid bot name</h2><a href='/'>Back</a>", status_code=400)

    existing = db.execute("SELECT id, team_id, active_version FROM bots WHERE name = ?", (safe_name,)).fetchone()
    if existing and existing["team_id"] != team["id"]:
        db.close()
        return HTMLResponse("<h2 style='color:red;'>Bot belongs to another team</h2><a href='/'>Back</a>", status_code=403)
    if not existing:
        bot_count = db.execute("SELECT COUNT(*) as cnt FROM bots WHERE team_id = ?", (team["id"],)).fetchone()["cnt"]
        if bot_count >= MAX_BOTS_PER_TEAM:
            db.close()
            return HTMLResponse(f"<h2 style='color:red;'>Maximum {MAX_BOTS_PER_TEAM} bots per team</h2><a href='/'>Back</a>", status_code=400)

    # Extract to temp dir
    tmp_dir = Path(tempfile.mkdtemp())
    test_dir = tmp_dir / safe_name
    test_dir.mkdir()

    content = await file.read()
    if len(content) > MAX_UPLOAD_SIZE:
        shutil.rmtree(tmp_dir)
        db.close()
        return HTMLResponse(f"<h2 style='color:red;'>File too large (max {MAX_UPLOAD_SIZE // 1024 // 1024}MB)</h2><a href='/'>Back</a>", status_code=413)
    err = _extract_upload(test_dir, content, file.filename or "MyBot.py")
    if err:
        shutil.rmtree(tmp_dir)
        db.close()
        return HTMLResponse(f"<h2 style='color:red;'>{err}</h2><a href='/'>Back</a>", status_code=400)

    # Detect language, copy libs, compile
    language, err = _setup_bot_language(test_dir)
    if not language:
        shutil.rmtree(tmp_dir)
        db.close()
        return HTMLResponse(f"<h2 style='color:red;'>{err}</h2><a href='/'>Back</a>", status_code=400)

    # Write run.sh for validation
    config = LANGUAGES[language]
    run_sh = test_dir / "run.sh"
    run_sh.write_text(f"#!/bin/sh\ncd {test_dir}\n{config['run']}\n")
    run_sh.chmod(0o755)

    # Validation: 50-turn test match
    test_map = next((ANTS_DIR / "maps" / "maze").glob("*p04*.map"))
    log_dir = tmp_dir / "logs"
    log_dir.mkdir()
    cmd = [
        "python3", str(ANTS_DIR / "playgame.py"),
        "--player_seed", "42", "--end_wait=0.1",
        "--turns", "50", "--turntime", "1000", "--loadtime", "3000",
        "--map_file", str(test_map), "--log_dir", str(log_dir), "-R",
        str(run_sh),
        str(BOTS_DIR / "HunterBot" / "run.sh"),
        str(BOTS_DIR / "LeftyBot" / "run.sh"),
        str(BOTS_DIR / "GreedyBot" / "run.sh"),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, cwd=str(ANTS_DIR))
    except subprocess.TimeoutExpired:
        shutil.rmtree(tmp_dir)
        db.close()
        return HTMLResponse("<h2 style='color:red;'>Bot timed out during validation</h2><a href='/'>Back</a>", status_code=400)

    replay_files = list(log_dir.glob("*.replay"))
    if not replay_files:
        shutil.rmtree(tmp_dir)
        db.close()
        return HTMLResponse(f"<h2 style='color:red;'>Bot failed to run</h2><pre>{result.stderr[:500]}</pre><a href='/'>Back</a>", status_code=400)

    with open(replay_files[0]) as f:
        rd = json.load(f)
    if "error" in rd:
        shutil.rmtree(tmp_dir)
        db.close()
        return HTMLResponse(f"<h2 style='color:red;'>Validation error</h2><pre>{rd['error'][:300]}</pre><a href='/'>Back</a>", status_code=400)

    # Validation passed — move to real bot dir
    bot_dir = BOTS_DIR / safe_name
    if bot_dir.exists():
        shutil.rmtree(bot_dir)
    shutil.copytree(test_dir, bot_dir)
    # Rewrite run.sh with final path
    final_run_sh = bot_dir / "run.sh"
    final_run_sh.write_text(f"#!/bin/sh\ncd {bot_dir}\n{config['run']}\n")
    final_run_sh.chmod(0o755)
    shutil.rmtree(tmp_dir)

    # Upsert in DB
    if existing:
        new_version = (existing["active_version"] or 0) + 1
        db.execute("UPDATE bots SET active_version = ?, language = ? WHERE id = ?",
                   (new_version, language, existing["id"]))
        db.execute("INSERT INTO bot_versions (bot_id, version, uploaded_at) VALUES (?, ?, ?)",
                   (existing["id"], new_version, datetime.now(MELB_TZ).isoformat()))
    else:
        db.execute("INSERT INTO bots (name, team_id, language, active_version) VALUES (?, ?, ?, 1)",
                   (safe_name, team["id"], language))
        db.commit()
        bot = db.execute("SELECT id FROM bots WHERE name = ?", (safe_name,)).fetchone()
        db.execute("INSERT INTO bot_versions (bot_id, version, uploaded_at) VALUES (?, 1, ?)",
                   (bot["id"], datetime.now(MELB_TZ).isoformat()))
    db.commit()
    db.close()

    return RedirectResponse(url=f"/bot/{safe_name}", status_code=303)


@app.get("/api/live")
async def live_feed():
    """SSE endpoint for live match results."""
    import asyncio
    from starlette.responses import StreamingResponse

    async def event_stream():
        last_id = 0
        db = get_db()
        row = db.execute("SELECT MAX(id) as max_id FROM matches").fetchone()
        if row and row["max_id"]:
            last_id = row["max_id"]
        db.close()

        while True:
            await asyncio.sleep(2)
            db = get_db()
            try:
                new_matches = db.execute("""
                    SELECT m.id, m.played_at, m.turns, m.map_file, m.replay_file
                    FROM matches m WHERE m.id > ? ORDER BY m.id
                """, (last_id,)).fetchall()
                for match in new_matches:
                    players = db.execute("""
                        SELECT mp.score, mp.status, mp.elo_change, b.name as bot_name
                        FROM match_players mp JOIN bots b ON mp.bot_id = b.id
                        WHERE mp.match_id = ? ORDER BY mp.player_index
                    """, (match["id"],)).fetchall()
                    data = json.dumps({
                        "match": dict(match),
                        "players": [dict(p) for p in players]
                    })
                    yield f"data: {data}\n\n"
                    last_id = match["id"]
            finally:
                db.close()

    return StreamingResponse(event_stream(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)
