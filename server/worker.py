"""AI Ants Challenge - Game Worker (runs as separate process)."""
import json
import os
import random
import resource
import signal
import subprocess
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

MELB_TZ = ZoneInfo("Australia/Melbourne")

from db import db_conn, db_readonly, dict_cursor, init_db
from glicko2 import update_ratings
from languages import get_run_command, LANGUAGES

BASE_DIR = Path(__file__).parent
BOTS_DIR = BASE_DIR / "bots"
REPLAYS_DIR = BASE_DIR / "replays"
ANTS_DIR = BASE_DIR.parent / "ants"

REPLAYS_DIR.mkdir(exist_ok=True)

MAX_REPLAYS = 50
MAX_MATCHES = 5000
MAX_ELO_HISTORY_PER_BOT = 200


# --- Sandboxing ---

def _set_limits():
    """Set resource limits for bot subprocess (called via preexec_fn)."""
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
    resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))


# --- Game Logic ---

def pick_map():
    maps_dir = ANTS_DIR / "maps"
    all_maps = []
    for subdir in maps_dir.iterdir():
        if subdir.is_dir():
            for f in subdir.glob("*p04*.map"):
                all_maps.append(f)
            for f in subdir.glob("*_04p*.map"):
                all_maps.append(f)
    if not all_maps:
        all_maps = list(maps_dir.rglob("*.map"))
    return random.choice(all_maps) if all_maps else None


def run_single_game(bot_ids, bot_names, bot_versions, bot_languages):
    """Run a single 4-player game with resource-limited bots."""
    map_file = pick_map()
    if not map_file:
        return None

    bot_cmds = []
    for name, lang in zip(bot_names, bot_languages):
        active_link = BOTS_DIR / name / "active"
        if active_link.exists():
            bot_dir = active_link.resolve()
        else:
            bot_dir = BOTS_DIR / name
        lang = lang or "python"
        config = LANGUAGES[lang]
        run_sh = bot_dir / "run.sh"
        if not run_sh.exists():
            run_sh.write_text(f"#!/bin/sh\ncd {bot_dir}\n{config['run']}\n")
            run_sh.chmod(0o755)
        bot_cmds.append(str(run_sh))

    game_id = f"{int(time.time())}_{random.randint(0, 9999)}"
    log_dir = REPLAYS_DIR / game_id
    log_dir.mkdir(exist_ok=True)

    cmd = [
        "python3", str(ANTS_DIR / "playgame.py"),
        "--player_seed", str(random.randint(0, 99999)),
        "--end_wait=0.25",
        "--turns", "1000",
        "--turntime", "1000",
        "--loadtime", "3000",
        "--map_file", str(map_file),
        "--log_dir", str(log_dir),
        "-R",
    ] + bot_cmds

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300,
            cwd=str(ANTS_DIR)
        )
    except subprocess.TimeoutExpired:
        return None

    replay_files = list(log_dir.glob("*.replay"))
    if not replay_files:
        print(f"[Worker] Game failed: {result.stderr[:300]}")
        return None

    with open(replay_files[0]) as f:
        replay_data = json.load(f)

    if "error" in replay_data:
        print(f"[Worker] Game error: {replay_data['error'][:200]}")
        return None

    replay_data["playernames"] = list(bot_names)
    with open(replay_files[0], "w") as f:
        json.dump(replay_data, f)

    return {
        "scores": replay_data["score"],
        "status": replay_data["status"],
        "turns": replay_data["game_length"],
        "map_file": str(map_file.relative_to(ANTS_DIR)),
        "replay_file": str(replay_files[0].relative_to(REPLAYS_DIR)),
    }


def cleanup_old_replays(conn, cur):
    """Keep only the last MAX_REPLAYS replay files on disk."""
    cur.execute(
        "SELECT id, replay_file FROM matches WHERE replay_file IS NOT NULL ORDER BY id DESC OFFSET %s",
        (MAX_REPLAYS,)
    )
    old_matches = cur.fetchall()
    for old in old_matches:
        old_path = (REPLAYS_DIR / old["replay_file"]).resolve()
        if not str(old_path).startswith(str(REPLAYS_DIR.resolve())):
            continue
        if old_path.exists():
            old_path.unlink()
        parent = old_path.parent
        if parent != REPLAYS_DIR and parent.exists() and not list(parent.iterdir()):
            parent.rmdir()
        cur.execute("UPDATE matches SET replay_file = NULL WHERE id = %s", (old["id"],))


def archive_old_data(conn, cur):
    """Prune old matches and ELO history to keep DB size bounded."""
    cur.execute(
        "SELECT id FROM matches ORDER BY id DESC LIMIT 1 OFFSET %s", (MAX_MATCHES,)
    )
    cutoff = cur.fetchone()
    if cutoff:
        cutoff_id = cutoff["id"]
        cur.execute("DELETE FROM match_players WHERE match_id <= %s", (cutoff_id,))
        cur.execute("DELETE FROM matches WHERE id <= %s", (cutoff_id,))

    cur.execute("SELECT id FROM bots")
    bots = cur.fetchall()
    for bot in bots:
        cur.execute("""
            DELETE FROM elo_history WHERE bot_id = %s AND id NOT IN (
                SELECT id FROM elo_history WHERE bot_id = %s ORDER BY id DESC LIMIT %s
            )
        """, (bot["id"], bot["id"], MAX_ELO_HISTORY_PER_BOT))


def select_match_bots(bots):
    """ELO-based matchmaking: pick a seed bot, then 3 closest by ELO."""
    bots = list(bots)
    seed = random.choice(bots)
    others = [b for b in bots if b["id"] != seed["id"]]
    others.sort(key=lambda b: abs(b["elo"] - seed["elo"]))
    pool = others[:min(6, len(others))]
    partners = random.sample(pool, min(3, len(pool)))
    return [seed] + partners


def game_loop():
    """Continuously pick 4 bots via ELO matchmaking and run matches."""
    print("[Worker] Starting game loop...")
    while True:
        try:
            with db_readonly() as conn:
                cur = dict_cursor(conn)
                cur.execute("SELECT id, name, elo, rd, volatility, active_version, language FROM bots WHERE active = 1")
                bots = cur.fetchall()

            if len(bots) < 4:
                print(f"[Worker] Only {len(bots)} bots, need 4. Waiting...")
                time.sleep(5)
                continue

            selected = select_match_bots(bots)
            bot_ids = [b["id"] for b in selected]
            bot_names = [b["name"] for b in selected]
            bot_versions = [b["active_version"] for b in selected]
            bot_languages = [b["language"] for b in selected]

            print(f"[Worker] Match: {bot_names}")
            result = run_single_game(bot_ids, bot_names, bot_versions, bot_languages)

            if result is None:
                time.sleep(2)
                continue

            # Glicko-2 update
            glicko_players = [
                {"rating": b["elo"], "rd": b["rd"] or 350, "vol": b["volatility"] or 0.06, "score": result["scores"][i]}
                for i, b in enumerate(selected)
            ]
            updated = update_ratings(glicko_players)

            with db_conn() as conn:
                cur = dict_cursor(conn)
                cur.execute(
                    "INSERT INTO matches (played_at, map_file, turns, replay_file) VALUES (%s, %s, %s, %s) RETURNING id",
                    (datetime.now(MELB_TZ).isoformat(), result["map_file"], result["turns"], result["replay_file"])
                )
                match_id = cur.fetchone()["id"]

                max_score = max(result["scores"])
                for i, bot_id in enumerate(bot_ids):
                    is_winner = 1 if result["scores"][i] == max_score else 0
                    elo_change = updated[i]["rating"] - glicko_players[i]["rating"]
                    cur.execute(
                        "INSERT INTO match_players (match_id, bot_id, bot_version, player_index, score, status, elo_change) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                        (match_id, bot_id, bot_versions[i], i, result["scores"][i], result["status"][i], elo_change)
                    )
                    cur.execute(
                        "UPDATE bots SET elo = %s, rd = %s, volatility = %s, games_played = games_played + 1, wins = wins + %s WHERE id = %s",
                        (updated[i]["rating"], updated[i]["rd"], updated[i]["vol"], is_winner, bot_id)
                    )
                    cur.execute(
                        "INSERT INTO elo_history (bot_id, match_id, elo, recorded_at) VALUES (%s, %s, %s, %s)",
                        (bot_id, match_id, updated[i]["rating"], datetime.now(MELB_TZ).isoformat())
                    )

                cleanup_old_replays(conn, cur)

                cur.execute("SELECT COUNT(*) as n FROM matches")
                match_count = cur.fetchone()["n"]
                if match_count % 100 == 0:
                    archive_old_data(conn, cur)

            elo_changes = [updated[i]["rating"] - glicko_players[i]["rating"] for i in range(len(selected))]
            print(f"[Worker] Done: scores={result['scores']} elo=[{', '.join(f'{c:+.1f}' for c in elo_changes)}]")
            time.sleep(1)

        except Exception as e:
            print(f"[Worker] Error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    init_db()
    game_loop()
