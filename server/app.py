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

from db import db_conn, db_readonly, dict_cursor, init_db
from languages import detect_language, compile_bot, get_starter_files_dir, LANGUAGES

import os
ADMIN_SECRET = os.environ.get("ADMIN_SECRET", "")

import resource

def _set_bot_limits():
    """Resource limits for bot subprocesses."""
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
    resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))

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
            sorted_keys = sorted(self._times, key=self._times.get)
            for k in sorted_keys[:self._max_entries // 4]:
                del self._times[k]
        return False


_upload_limiter = RateLimiter(cooldown=30)
_register_limiter = RateLimiter(cooldown=10)
_test_limiter = RateLimiter(cooldown=10)
_admin_limiter = RateLimiter(cooldown=5)


def _verify_token(token: str):
    """Verify a team token using constant-time comparison. Returns team row or None."""
    with db_readonly() as conn:
        cur = dict_cursor(conn)
        cur.execute("SELECT id, name, token FROM teams")
        teams = cur.fetchall()
    for team in teams:
        if hmac.compare_digest(team["token"], token):
            return {"id": team["id"], "name": team["name"]}
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


@app.get("/health")
def health():
    try:
        with db_readonly() as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1")
        return {"status": "ok", "db": "connected"}
    except Exception:
        return JSONResponse({"status": "error", "db": "disconnected"}, status_code=503)


@app.on_event("startup")
def startup():
    init_db()
    if os.environ.get("SEED_SAMPLE_BOTS", "").lower() in ("1", "true", "yes"):
        _seed_sample_bots()


def _seed_sample_bots():
    """Copy sample bots from the image into the bots volume and register them in the DB."""
    sample_src = Path("/app/sample_bots")
    if not sample_src.exists():
        return

    # Copy bot files into volume if not already present
    for bot_dir in sample_src.iterdir():
        if bot_dir.is_dir():
            dest = BOTS_DIR / bot_dir.name
            if not dest.exists():
                shutil.copytree(bot_dir, dest)

    with db_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("SELECT id FROM teams WHERE name = 'Sample'")
        team = cur.fetchone()
        if not team:
            token = "sample-" + secrets.token_hex(8)
            cur.execute("INSERT INTO teams (name, token, created_at) VALUES (%s, %s, %s) RETURNING id",
                        ("Sample", token, datetime.now(MELB_TZ).isoformat()))
            team = cur.fetchone()
        team_id = team["id"]

        for bot_dir in BOTS_DIR.iterdir():
            if bot_dir.is_dir() and (bot_dir / "active").exists():
                continue
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
                cur.execute("SELECT id FROM bots WHERE name = %s", (name,))
                existing = cur.fetchone()
                if not existing:
                    cur.execute("INSERT INTO bots (name, team_id, language, active_version) VALUES (%s, %s, %s, 1) RETURNING id",
                                (name, team_id, lang))
                    bot = cur.fetchone()
                    cur.execute("INSERT INTO bot_versions (bot_id, version, uploaded_at) VALUES (%s, 1, %s)",
                                (bot["id"], datetime.now(MELB_TZ).isoformat()))


# --- Pages ---

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    page = max(1, int(request.query_params.get("page", "1")))
    per_page = 10
    offset = (page - 1) * per_page

    with db_readonly() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT b.*, t.name as team_name FROM bots b
            JOIN teams t ON b.team_id = t.id ORDER BY b.elo DESC
        """)
        bots = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT COUNT(*) as cnt FROM matches")
        total_matches = cur.fetchone()["cnt"]
        total_pages = max(1, (total_matches + per_page - 1) // per_page)
        cur.execute("""
            SELECT m.id, m.played_at, m.turns, m.map_file, m.replay_file,
                   mp.bot_id, mp.bot_version, mp.player_index, mp.score, mp.status, mp.elo_change,
                   b.name as bot_name
            FROM matches m
            JOIN match_players mp ON mp.match_id = m.id
            JOIN bots b ON mp.bot_id = b.id
            WHERE m.id IN (SELECT id FROM matches ORDER BY id DESC LIMIT %s OFFSET %s)
            ORDER BY m.id DESC, mp.player_index
        """, (per_page, offset))
        rows = cur.fetchall()

    matches_with_players = []
    current_match = None
    for row in rows:
        row = dict(row)
        if not current_match or current_match["match"]["id"] != row["id"]:
            current_match = {"match": {"id": row["id"], "played_at": row["played_at"], "turns": row["turns"],
                                        "map_file": row["map_file"], "replay_file": row["replay_file"]}, "players": []}
            matches_with_players.append(current_match)
        current_match["players"].append({"bot_name": row["bot_name"], "bot_version": row["bot_version"],
                                          "score": row["score"], "status": row["status"], "elo_change": row["elo_change"]})
    html = jinja_env.get_template("index.html").render(bots=bots, matches=matches_with_players,
                                                        page=page, total_pages=total_pages)
    return HTMLResponse(html)


@app.get("/replay/{match_id}", response_class=HTMLResponse)
def view_replay(match_id: int):
    with db_readonly() as conn:
        cur = dict_cursor(conn)
        cur.execute("SELECT * FROM matches WHERE id = %s", (match_id,))
        match = cur.fetchone()
    if not match or not match["replay_file"]:
        raise HTTPException(404, "Replay not found")
    return HTMLResponse(jinja_env.get_template("replay.html").render(match_id=match_id))


@app.get("/replay_data/{match_id}")
def replay_data(match_id: int):
    with db_readonly() as conn:
        cur = dict_cursor(conn)
        cur.execute("SELECT replay_file FROM matches WHERE id = %s", (match_id,))
        match = cur.fetchone()
    if not match or not match["replay_file"]:
        raise HTTPException(404, "Replay not found")
    replay_path = (REPLAYS_DIR / match["replay_file"]).resolve()
    if not str(replay_path).startswith(str(REPLAYS_DIR.resolve())):
        raise HTTPException(403, "Invalid replay path")
    if not replay_path.exists():
        raise HTTPException(404, "Replay file missing")
    return FileResponse(replay_path, media_type="application/json")


@app.get("/bot/{bot_name}", response_class=HTMLResponse)
def bot_profile(bot_name: str, request: Request):
    matches_page = max(1, int(request.query_params.get("matches_page", "1")))
    versions_page = max(1, int(request.query_params.get("versions_page", "1")))
    per_page = 10

    with db_readonly() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT b.*, t.name as team_name FROM bots b
            JOIN teams t ON b.team_id = t.id WHERE b.name = %s
        """, (bot_name,))
        bot = cur.fetchone()
        if not bot:
            raise HTTPException(404, "Bot not found")
        bot = dict(bot)

        cur.execute("SELECT COUNT(*) as cnt FROM bot_versions WHERE bot_id = %s", (bot["id"],))
        versions_total = cur.fetchone()["cnt"]
        versions_total_pages = max(1, (versions_total + per_page - 1) // per_page)
        cur.execute("SELECT * FROM bot_versions WHERE bot_id = %s ORDER BY version DESC LIMIT %s OFFSET %s",
                    (bot["id"], per_page, (versions_page - 1) * per_page))
        versions = [dict(r) for r in cur.fetchall()]

        cur.execute("SELECT COUNT(*) as cnt FROM match_players WHERE bot_id = %s", (bot["id"],))
        matches_total = cur.fetchone()["cnt"]
        matches_total_pages = max(1, (matches_total + per_page - 1) // per_page)
        cur.execute("""
            SELECT mp.*, m.played_at, m.turns, m.map_file, m.replay_file
            FROM match_players mp JOIN matches m ON mp.match_id = m.id
            WHERE mp.bot_id = %s ORDER BY m.id DESC LIMIT %s OFFSET %s
        """, (bot["id"], per_page, (matches_page - 1) * per_page))
        matches = [dict(r) for r in cur.fetchall()]

    html = jinja_env.get_template("bot_profile.html").render(
        bot=bot, versions=versions, matches=matches,
        matches_page=matches_page, matches_total_pages=matches_total_pages,
        versions_page=versions_page, versions_total_pages=versions_total_pages)
    return HTMLResponse(html)


# --- API ---

@app.post("/api/register")
def register_team(name: str = Form(...), invite_code: str = Form(...), request: Request = None):
    """Register a new team with an invite code and get an auth token."""
    client_ip = request.client.host if request and request.client else "unknown"
    if _register_limiter.is_limited(client_ip):
        raise HTTPException(429, "Too many registrations. Try again later.")

    with db_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("SELECT code, used_by FROM invite_codes WHERE code = %s", (invite_code,))
        invite = cur.fetchone()
        if not invite:
            raise HTTPException(400, "Invalid invite code")
        if invite["used_by"] is not None:
            raise HTTPException(400, "Invite code already used")

        cur.execute("SELECT id FROM teams WHERE name = %s", (name,))
        existing = cur.fetchone()
        if existing:
            raise HTTPException(400, "Team name already taken")
        token = secrets.token_hex(16)
        cur.execute("INSERT INTO teams (name, token, created_at) VALUES (%s, %s, %s) RETURNING id",
                    (name, token, datetime.now(MELB_TZ).isoformat()))
        team = cur.fetchone()
        cur.execute("UPDATE invite_codes SET used_by = %s, used_at = %s WHERE code = %s",
                    (team["id"], datetime.now(MELB_TZ).isoformat(), invite_code))
    return {"team": name, "token": token}


@app.post("/api/admin/invite-codes")
def generate_invite_codes(count: int = Form(default=1), authorization: str = Header(...), request: Request = None):
    """Generate invite codes. Requires ADMIN_SECRET in Authorization header."""
    client_ip = request.client.host if request and request.client else "unknown"
    if _admin_limiter.is_limited(client_ip):
        raise HTTPException(429, "Too many requests")
    if not ADMIN_SECRET or not hmac.compare_digest(authorization, ADMIN_SECRET):
        raise HTTPException(403, "Forbidden")
    codes = [secrets.token_urlsafe(12) for _ in range(min(count, 50))]
    with db_conn() as conn:
        cur = conn.cursor()
        for code in codes:
            cur.execute("INSERT INTO invite_codes (code, created_at) VALUES (%s, %s)",
                        (code, datetime.now(MELB_TZ).isoformat()))
    return {"codes": codes}


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
    elif any(filename.endswith(ext) for ext in (".py", ".java", ".cc", ".cpp", ".go", ".js", ".rb", ".cs")):
        ext = Path(filename).suffix
        entry_names = {"py": "MyBot.py", "java": "MyBot.java", "cc": "MyBot.cc",
                       "cpp": "MyBot.cpp", "go": "MyBot.go", "js": "MyBot.js", "rb": "MyBot.rb", "cs": "MyBot.cs"}
        target = entry_names.get(ext.lstrip("."), filename)
        (bot_dir / target).write_bytes(content)
    else:
        return "Upload .zip or a source file (.py, .java, .cc, .cpp, .go, .js, .rb, .cs)"
    return None


def _setup_bot_language(bot_dir: Path) -> tuple[str | None, str]:
    """Detect language, copy starter libs, compile. Returns (language, error)."""
    language = detect_language(bot_dir)
    if not language:
        return None, "Could not detect language. Ensure your entry point is named MyBot.py/java/cc/cpp/go/js/rb/cs"

    starter_dir = get_starter_files_dir(language, ANTS_DIR)
    if starter_dir:
        for f in starter_dir.iterdir():
            if f.is_file() and f.name.startswith("MyBot"):
                continue
            if f.is_file() and not (bot_dir / f.name).exists():
                shutil.copy(f, bot_dir / f.name)

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

    if _upload_limiter.is_limited(token):
        raise HTTPException(429, "Upload too frequent. Wait 30 seconds between uploads.")

    team = _verify_token(token)
    if not team:
        raise HTTPException(401, "Invalid token")

    safe_name = "".join(c for c in bot_name if c.isalnum() or c in "-_")[:MAX_BOT_NAME_LENGTH]
    if not safe_name:
        raise HTTPException(400, "Invalid bot name")

    with db_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("SELECT id, team_id, active_version FROM bots WHERE name = %s", (safe_name,))
        existing = cur.fetchone()
        if existing and existing["team_id"] != team["id"]:
            raise HTTPException(403, "Bot belongs to another team")
        if not existing:
            cur.execute("SELECT COUNT(*) as cnt FROM bots WHERE team_id = %s", (team["id"],))
            bot_count = cur.fetchone()["cnt"]
            if bot_count >= MAX_BOTS_PER_TEAM:
                raise HTTPException(400, f"Maximum {MAX_BOTS_PER_TEAM} bots per team")

        bot_dir = BOTS_DIR / safe_name
        bot_dir.mkdir(exist_ok=True)
        new_version = (existing["active_version"] or 0) + 1 if existing else 1
        version_dir = bot_dir / f"v{new_version}"
        if version_dir.exists():
            shutil.rmtree(version_dir)
        version_dir.mkdir()

        content = await file.read()
        if len(content) > MAX_UPLOAD_SIZE:
            shutil.rmtree(version_dir)
            raise HTTPException(413, f"File too large. Maximum size is {MAX_UPLOAD_SIZE // 1024 // 1024}MB.")
        err = _extract_upload(version_dir, content, file.filename or "MyBot.py")
        if err:
            shutil.rmtree(version_dir)
            raise HTTPException(400, err)

        language = detect_language(version_dir)
        if not language:
            shutil.rmtree(version_dir)
            raise HTTPException(400, "Could not detect language. Ensure your entry point is named MyBot.py/java/cc/cpp/go/js/rb/cs")

        # Do compilation in background thread
        import threading
        def _compile_and_activate():
            _lang, err = _setup_bot_language(version_dir)
            if err:
                shutil.rmtree(version_dir)
                return

            active_link = bot_dir / "active"
            if active_link.exists() or active_link.is_symlink():
                active_link.unlink()
            active_link.symlink_to(version_dir)

            config = LANGUAGES[_lang]
            run_sh = version_dir / "run.sh"
            run_sh.write_text(f"#!/bin/sh\ncd {version_dir}\n{config['run']}\n")
            run_sh.chmod(0o755)

        threading.Thread(target=_compile_and_activate, daemon=True).start()

        if existing:
            cur.execute("UPDATE bots SET active_version = %s, language = %s WHERE id = %s",
                        (new_version, language, existing["id"]))
            cur.execute("INSERT INTO bot_versions (bot_id, version, uploaded_at) VALUES (%s, %s, %s)",
                        (existing["id"], new_version, datetime.now(MELB_TZ).isoformat()))
        else:
            cur.execute("INSERT INTO bots (name, team_id, language, active_version) VALUES (%s, %s, %s, 1) RETURNING id",
                        (safe_name, team["id"], language))
            bot = cur.fetchone()
            cur.execute("INSERT INTO bot_versions (bot_id, version, uploaded_at) VALUES (%s, 1, %s)",
                        (bot["id"], datetime.now(MELB_TZ).isoformat()))

    MAX_VERSIONS = 5
    version_dirs = sorted(
        [d for d in (BOTS_DIR / safe_name).iterdir() if d.is_dir() and d.name.startswith("v")],
        key=lambda d: int(d.name[1:]),
        reverse=True
    )
    for old_dir in version_dirs[MAX_VERSIONS:]:
        shutil.rmtree(old_dir)

    return {"status": "ok", "bot": safe_name, "language": language, "version": new_version if existing else 1}


@app.get("/api/leaderboard")
def api_leaderboard():
    with db_readonly() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT b.name, t.name as team, b.elo, b.rd, b.games_played, b.wins, b.language, b.active
            FROM bots b JOIN teams t ON b.team_id = t.id ORDER BY b.elo DESC
        """)
        bots = cur.fetchall()
    return [dict(b) for b in bots]


@app.post("/api/bot/{bot_name}/deactivate")
def deactivate_bot(bot_name: str, authorization: str = Header(...)):
    """Deactivate a bot (remove from matchmaking). Requires team token."""
    token = authorization.replace("Bearer ", "").strip()
    team = _verify_token(token)
    if not team:
        raise HTTPException(401, "Invalid token")
    with db_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("SELECT id, team_id FROM bots WHERE name = %s", (bot_name,))
        bot = cur.fetchone()
        if not bot:
            raise HTTPException(404, "Bot not found")
        if bot["team_id"] != team["id"]:
            raise HTTPException(403, "Bot belongs to another team")
        cur.execute("UPDATE bots SET active = 0 WHERE id = %s", (bot["id"],))
    return {"status": "ok", "bot": bot_name, "active": False}


@app.post("/api/bot/{bot_name}/activate")
def activate_bot(bot_name: str, authorization: str = Header(...)):
    """Reactivate a bot for matchmaking. Requires team token."""
    token = authorization.replace("Bearer ", "").strip()
    team = _verify_token(token)
    if not team:
        raise HTTPException(401, "Invalid token")
    with db_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("SELECT id, team_id FROM bots WHERE name = %s", (bot_name,))
        bot = cur.fetchone()
        if not bot:
            raise HTTPException(404, "Bot not found")
        if bot["team_id"] != team["id"]:
            raise HTTPException(403, "Bot belongs to another team")
        cur.execute("UPDATE bots SET active = 1 WHERE id = %s", (bot["id"],))
    return {"status": "ok", "bot": bot_name, "active": True}


@app.post("/api/bot/{bot_name}/rollback/{version}")
def rollback_bot(bot_name: str, version: int, authorization: str = Header(...)):
    """Roll back a bot to a previous version. Requires team token."""
    token = authorization.replace("Bearer ", "").strip()
    team = _verify_token(token)
    if not team:
        raise HTTPException(401, "Invalid token")
    with db_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("SELECT id, team_id FROM bots WHERE name = %s", (bot_name,))
        bot = cur.fetchone()
        if not bot:
            raise HTTPException(404, "Bot not found")
        if bot["team_id"] != team["id"]:
            raise HTTPException(403, "Bot belongs to another team")
        version_dir = BOTS_DIR / bot_name / f"v{version}"
        if not version_dir.exists():
            raise HTTPException(404, f"Version {version} not found on disk")
        active_link = BOTS_DIR / bot_name / "active"
        if active_link.exists() or active_link.is_symlink():
            active_link.unlink()
        active_link.symlink_to(version_dir)
        cur.execute("UPDATE bots SET active_version = %s WHERE id = %s", (version, bot["id"]))
    return {"status": "ok", "bot": bot_name, "active_version": version}


@app.delete("/api/bot/{bot_name}")
def delete_bot(bot_name: str, authorization: str = Header(...)):
    """Delete a bot entirely. Requires team token."""
    token = authorization.replace("Bearer ", "").strip()
    team = _verify_token(token)
    if not team:
        raise HTTPException(401, "Invalid token")
    with db_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("SELECT id, team_id FROM bots WHERE name = %s", (bot_name,))
        bot = cur.fetchone()
        if not bot:
            raise HTTPException(404, "Bot not found")
        if bot["team_id"] != team["id"]:
            raise HTTPException(403, "Bot belongs to another team")
        cur.execute("DELETE FROM elo_history WHERE bot_id = %s", (bot["id"],))
        cur.execute("DELETE FROM match_players WHERE bot_id = %s", (bot["id"],))
        cur.execute("DELETE FROM bot_versions WHERE bot_id = %s", (bot["id"],))
        cur.execute("DELETE FROM bots WHERE id = %s", (bot["id"],))
    bot_dir = BOTS_DIR / bot_name
    if bot_dir.exists():
        shutil.rmtree(bot_dir)
    return {"status": "ok", "bot": bot_name, "deleted": True}


@app.get("/api/matches")
def api_matches():
    with db_readonly() as conn:
        cur = dict_cursor(conn)
        cur.execute("SELECT * FROM matches ORDER BY id DESC LIMIT 50")
        matches = cur.fetchall()
    return [dict(m) for m in matches]


@app.get("/api/elo_history/{bot_name}")
def api_elo_history(bot_name: str):
    with db_readonly() as conn:
        cur = dict_cursor(conn)
        cur.execute("SELECT id FROM bots WHERE name = %s", (bot_name,))
        bot = cur.fetchone()
        if not bot:
            raise HTTPException(404, "Bot not found")
        cur.execute(
            "SELECT elo, recorded_at FROM elo_history WHERE bot_id = %s ORDER BY id DESC LIMIT 50",
            (bot["id"],)
        )
        history = cur.fetchall()
    return [dict(h) for h in reversed(history)]


@app.get("/api/sparklines")
def api_sparklines():
    """Return last 20 ELO values for each active bot (for leaderboard sparklines)."""
    with db_readonly() as conn:
        cur = dict_cursor(conn)
        cur.execute("SELECT id, name FROM bots WHERE active = 1")
        bots = cur.fetchall()
        result = {}
        for bot in bots:
            cur.execute(
                "SELECT elo FROM elo_history WHERE bot_id = %s ORDER BY id DESC LIMIT 20",
                (bot["id"],)
            )
            elos = [r["elo"] for r in reversed(cur.fetchall())]
            if elos:
                result[bot["name"]] = elos
    return result


@app.get("/api/head_to_head/{bot_a}/{bot_b}")
def api_head_to_head(bot_a: str, bot_b: str):
    """Get head-to-head stats between two bots."""
    with db_readonly() as conn:
        cur = dict_cursor(conn)
        cur.execute("SELECT id FROM bots WHERE name = %s", (bot_a,))
        a = cur.fetchone()
        cur.execute("SELECT id FROM bots WHERE name = %s", (bot_b,))
        b = cur.fetchone()
        if not a or not b:
            raise HTTPException(404, "Bot not found")
        cur.execute("""
            SELECT ma.match_id, ma.score as score_a, mb.score as score_b
            FROM match_players ma
            JOIN match_players mb ON ma.match_id = mb.match_id
            WHERE ma.bot_id = %s AND mb.bot_id = %s
        """, (a["id"], b["id"]))
        rows = cur.fetchall()
    wins_a = sum(1 for r in rows if r["score_a"] > r["score_b"])
    wins_b = sum(1 for r in rows if r["score_b"] > r["score_a"])
    draws = sum(1 for r in rows if r["score_a"] == r["score_b"])
    return {"bot_a": bot_a, "bot_b": bot_b, "matches": len(rows),
            "wins_a": wins_a, "wins_b": wins_b, "draws": draws}


@app.post("/api/test")
async def test_match(
    bot_name: str = Form(...),
    file: UploadFile = File(...),
    authorization: str = Header(...),
    request: Request = None,
):
    """Run a test match against sample bots without affecting ELO. Requires auth."""
    import subprocess
    import tempfile
    import random

    token = authorization.replace("Bearer ", "").strip()
    team = _verify_token(token)
    if not team:
        raise HTTPException(401, "Invalid token")

    if _test_limiter.is_limited(token):
        raise HTTPException(429, "Too many test requests. Wait 10 seconds.")

    safe_name = "".join(c for c in bot_name if c.isalnum() or c in "-_") or "TestBot"

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

    if not (bot_dir / "ants.py").exists():
        shutil.copy(ANTS_DIR / "dist" / "sample_bots" / "python" / "ants.py", bot_dir / "ants.py")

    sample_bots = ["HunterBot", "LeftyBot", "GreedyBot"]
    opponents = [f"python3 {BOTS_DIR / name / 'MyBot.py'}" for name in sample_bots]

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
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, cwd=str(ANTS_DIR), preexec_fn=_set_bot_limits)
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
    """Web form upload — saves file, returns immediately, compiles in background."""
    import threading

    if _upload_limiter.is_limited(team_token):
        return JSONResponse({"error": "Upload too frequent. Wait 30 seconds."}, status_code=429)

    team = _verify_token(team_token)
    if not team:
        return JSONResponse({"error": "Invalid token"}, status_code=401)

    safe_name = "".join(c for c in bot_name if c.isalnum() or c in "-_")[:MAX_BOT_NAME_LENGTH]
    if not safe_name:
        return JSONResponse({"error": "Invalid bot name"}, status_code=400)

    with db_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("SELECT id, team_id, active_version FROM bots WHERE name = %s", (safe_name,))
        existing = cur.fetchone()
        if existing and existing["team_id"] != team["id"]:
            return JSONResponse({"error": "Bot belongs to another team"}, status_code=403)
        if not existing:
            cur.execute("SELECT COUNT(*) as cnt FROM bots WHERE team_id = %s", (team["id"],))
            bot_count = cur.fetchone()["cnt"]
            if bot_count >= MAX_BOTS_PER_TEAM:
                return JSONResponse({"error": f"Maximum {MAX_BOTS_PER_TEAM} bots per team"}, status_code=400)

    content = await file.read()
    if len(content) > MAX_UPLOAD_SIZE:
        return JSONResponse({"error": f"File too large (max {MAX_UPLOAD_SIZE // 1024 // 1024}MB)"}, status_code=413)

    # Generate upload ID and save to pending
    import uuid
    upload_id = str(uuid.uuid4())[:8]
    _upload_status[upload_id] = {"status": "processing", "message": "Extracting files..."}

    def process_upload():
        import tempfile
        try:
            tmp_dir = Path(tempfile.mkdtemp())
            test_dir = tmp_dir / safe_name
            test_dir.mkdir()

            err = _extract_upload(test_dir, content, file.filename or "MyBot.py")
            if err:
                shutil.rmtree(tmp_dir)
                _upload_status[upload_id] = {"status": "error", "message": err}
                return

            _upload_status[upload_id]["message"] = "Detecting language & compiling..."
            language, err = _setup_bot_language(test_dir)
            if not language:
                shutil.rmtree(tmp_dir)
                _upload_status[upload_id] = {"status": "error", "message": err}
                return

            _upload_status[upload_id]["message"] = "Installing bot..."
            config = LANGUAGES[language]
            bot_dir = BOTS_DIR / safe_name
            if bot_dir.exists():
                shutil.rmtree(bot_dir)
            shutil.copytree(test_dir, bot_dir)
            run_sh = bot_dir / "run.sh"
            run_sh.write_text(f"#!/bin/sh\ncd {bot_dir}\n{config['run']}\n")
            run_sh.chmod(0o755)
            shutil.rmtree(tmp_dir)

            with db_conn() as conn:
                cur = dict_cursor(conn)
                cur.execute("SELECT id, active_version FROM bots WHERE name = %s", (safe_name,))
                existing = cur.fetchone()
                if existing:
                    new_version = (existing["active_version"] or 0) + 1
                    cur.execute("UPDATE bots SET active_version = %s, language = %s WHERE id = %s",
                                (new_version, language, existing["id"]))
                    cur.execute("INSERT INTO bot_versions (bot_id, version, uploaded_at) VALUES (%s, %s, %s)",
                                (existing["id"], new_version, datetime.now(MELB_TZ).isoformat()))
                else:
                    cur.execute("INSERT INTO bots (name, team_id, language, active_version) VALUES (%s, %s, %s, 1) RETURNING id",
                                (safe_name, team["id"], language))
                    bot = cur.fetchone()
                    cur.execute("INSERT INTO bot_versions (bot_id, version, uploaded_at) VALUES (%s, 1, %s)",
                                (bot["id"], datetime.now(MELB_TZ).isoformat()))

            _upload_status[upload_id] = {"status": "done", "message": "Bot uploaded successfully!", "bot_name": safe_name}
        except Exception as e:
            _upload_status[upload_id] = {"status": "error", "message": f"Unexpected error: {str(e)[:200]}"}

    threading.Thread(target=process_upload, daemon=True).start()
    return JSONResponse({"success": True, "upload_id": upload_id})


# Upload status tracking
_upload_status: dict = {}


@app.get("/api/upload_status/{upload_id}")
def get_upload_status(upload_id: str):
    """Poll upload processing status."""
    status = _upload_status.get(upload_id)
    if not status:
        raise HTTPException(404, "Unknown upload ID")
    # Clean up completed statuses after retrieval
    if status["status"] in ("done", "error"):
        _upload_status.pop(upload_id, None)
    return JSONResponse(status)


@app.get("/api/live")
async def live_feed():
    """SSE endpoint for live match results."""
    import asyncio
    from starlette.responses import StreamingResponse

    async def event_stream():
        last_id = 0
        with db_readonly() as conn:
            cur = dict_cursor(conn)
            cur.execute("SELECT MAX(id) as max_id FROM matches")
            row = cur.fetchone()
            if row and row["max_id"]:
                last_id = row["max_id"]

        while True:
            await asyncio.sleep(2)
            with db_readonly() as conn:
                cur = dict_cursor(conn)
                cur.execute("""
                    SELECT m.id, m.played_at, m.turns, m.map_file, m.replay_file
                    FROM matches m WHERE m.id > %s ORDER BY m.id
                """, (last_id,))
                new_matches = cur.fetchall()
                for match in new_matches:
                    cur.execute("""
                        SELECT mp.score, mp.status, mp.elo_change, mp.bot_version, b.name as bot_name
                        FROM match_players mp JOIN bots b ON mp.bot_id = b.id
                        WHERE mp.match_id = %s ORDER BY mp.player_index
                    """, (match["id"],))
                    players = cur.fetchall()
                    data = json.dumps({
                        "match": dict(match),
                        "players": [dict(p) for p in players]
                    })
                    yield f"data: {data}\n\n"
                    last_id = match["id"]

    return StreamingResponse(event_stream(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)
