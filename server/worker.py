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

from db import get_db, init_db
from languages import get_run_command, LANGUAGES

BASE_DIR = Path(__file__).parent
BOTS_DIR = BASE_DIR / "bots"
REPLAYS_DIR = BASE_DIR / "replays"
ANTS_DIR = BASE_DIR.parent / "ants"

REPLAYS_DIR.mkdir(exist_ok=True)

MAX_REPLAYS = 50


# --- ELO ---

def expected_score(ra, rb):
    return 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))


def update_elo(players_elo, scores, k=32):
    n = len(players_elo)
    changes = [0.0] * n
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            expected = expected_score(players_elo[i], players_elo[j])
            if scores[i] > scores[j]:
                actual = 1.0
            elif scores[i] == scores[j]:
                actual = 0.5
            else:
                actual = 0.0
            changes[i] += (k / (n - 1)) * (actual - expected)
    return changes


# --- Sandboxing ---

def _set_limits():
    """Set resource limits for bot subprocess (called via preexec_fn)."""
    # 512MB memory limit
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    # 60s CPU time limit
    resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
    # No new child processes
    resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))


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
        bot_dir = BOTS_DIR / name
        lang = lang or "python"
        config = LANGUAGES[lang]
        # Ensure run.sh exists
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

    # Inject correct bot names
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


def cleanup_old_replays(db):
    """Keep only the last MAX_REPLAYS replay files on disk."""
    old_matches = db.execute(
        "SELECT id, replay_file FROM matches WHERE replay_file IS NOT NULL ORDER BY id DESC LIMIT -1 OFFSET ?",
        (MAX_REPLAYS,)
    ).fetchall()
    for old in old_matches:
        old_path = (REPLAYS_DIR / old["replay_file"]).resolve()
        if not str(old_path).startswith(str(REPLAYS_DIR.resolve())):
            continue
        if old_path.exists():
            old_path.unlink()
        parent = old_path.parent
        if parent != REPLAYS_DIR and parent.exists() and not list(parent.iterdir()):
            parent.rmdir()
        db.execute("UPDATE matches SET replay_file = NULL WHERE id = ?", (old["id"],))
    if old_matches:
        db.commit()


def select_match_bots(bots):
    """ELO-based matchmaking: pick a seed bot, then 3 closest by ELO."""
    bots = list(bots)
    seed = random.choice(bots)
    others = [b for b in bots if b["id"] != seed["id"]]
    # Sort by ELO distance from seed, pick 3 closest with some randomness
    others.sort(key=lambda b: abs(b["elo"] - seed["elo"]))
    # Take from top 6 closest (or all if fewer) to add variety
    pool = others[:min(6, len(others))]
    partners = random.sample(pool, min(3, len(pool)))
    return [seed] + partners


def game_loop():
    """Continuously pick 4 bots via ELO matchmaking and run matches."""
    print("[Worker] Starting game loop...")
    while True:
        db = None
        try:
            db = get_db()
            bots = db.execute("SELECT id, name, elo, active_version, language FROM bots WHERE active = 1").fetchall()
            db.close()
            db = None

            if len(bots) < 4:
                print(f"[Worker] Only {len(bots)} bots, need 4. Waiting...")
                time.sleep(5)
                continue

            selected = select_match_bots(bots)
            bot_ids = [b["id"] for b in selected]
            bot_names = [b["name"] for b in selected]
            bot_elos = [b["elo"] for b in selected]
            bot_versions = [b["active_version"] for b in selected]
            bot_languages = [b["language"] for b in selected]

            print(f"[Worker] Match: {bot_names}")
            result = run_single_game(bot_ids, bot_names, bot_versions, bot_languages)

            if result is None:
                time.sleep(2)
                continue

            elo_changes = update_elo(bot_elos, result["scores"])

            db = get_db()
            cur = db.execute(
                "INSERT INTO matches (played_at, map_file, turns, replay_file) VALUES (?, ?, ?, ?)",
                (datetime.now(MELB_TZ).isoformat(), result["map_file"], result["turns"], result["replay_file"])
            )
            match_id = cur.lastrowid

            max_score = max(result["scores"])
            for i, bot_id in enumerate(bot_ids):
                is_winner = 1 if result["scores"][i] == max_score else 0
                db.execute(
                    "INSERT INTO match_players (match_id, bot_id, bot_version, player_index, score, status, elo_change) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (match_id, bot_id, bot_versions[i], i, result["scores"][i], result["status"][i], elo_changes[i])
                )
                db.execute(
                    "UPDATE bots SET elo = elo + ?, games_played = games_played + 1, wins = wins + ? WHERE id = ?",
                    (elo_changes[i], is_winner, bot_id)
                )
                # Record ELO history
                new_elo = bot_elos[i] + elo_changes[i]
                db.execute(
                    "INSERT INTO elo_history (bot_id, match_id, elo, recorded_at) VALUES (?, ?, ?, ?)",
                    (bot_id, match_id, new_elo, datetime.now(MELB_TZ).isoformat())
                )
            db.commit()

            cleanup_old_replays(db)
            db.close()

            print(f"[Worker] Done: scores={result['scores']} elo=[{', '.join(f'{c:+.1f}' for c in elo_changes)}]")
            time.sleep(1)

        except Exception as e:
            print(f"[Worker] Error: {e}")
            if db:
                try:
                    db.close()
                except Exception:
                    pass
            time.sleep(5)


if __name__ == "__main__":
    init_db()
    game_loop()
