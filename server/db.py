"""Shared database setup and helpers."""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "ants_challenge.db"
DB_PATH.parent.mkdir(exist_ok=True)


def get_db():
    db = sqlite3.connect(str(DB_PATH), timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=5000")
    return db


def init_db():
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS teams (
            id INTEGER PRIMARY KEY,
            name TEXT UNIQUE NOT NULL,
            token TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS bots (
            id INTEGER PRIMARY KEY,
            team_id INTEGER NOT NULL,
            name TEXT UNIQUE NOT NULL,
            elo REAL DEFAULT 1200,
            games_played INTEGER DEFAULT 0,
            wins INTEGER DEFAULT 0,
            active_version INTEGER,
            FOREIGN KEY (team_id) REFERENCES teams(id)
        );
        CREATE TABLE IF NOT EXISTS bot_versions (
            id INTEGER PRIMARY KEY,
            bot_id INTEGER NOT NULL,
            version INTEGER NOT NULL,
            uploaded_at TEXT NOT NULL,
            FOREIGN KEY (bot_id) REFERENCES bots(id)
        );
        CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY,
            played_at TEXT NOT NULL,
            map_file TEXT NOT NULL,
            turns INTEGER,
            replay_file TEXT
        );
        CREATE TABLE IF NOT EXISTS match_players (
            match_id INTEGER NOT NULL,
            bot_id INTEGER NOT NULL,
            bot_version INTEGER,
            player_index INTEGER,
            score INTEGER,
            status TEXT,
            elo_change REAL,
            FOREIGN KEY (match_id) REFERENCES matches(id),
            FOREIGN KEY (bot_id) REFERENCES bots(id)
        );
        CREATE TABLE IF NOT EXISTS elo_history (
            id INTEGER PRIMARY KEY,
            bot_id INTEGER NOT NULL,
            match_id INTEGER NOT NULL,
            elo REAL NOT NULL,
            recorded_at TEXT NOT NULL,
            FOREIGN KEY (bot_id) REFERENCES bots(id),
            FOREIGN KEY (match_id) REFERENCES matches(id)
        );
    """)
    db.commit()
    db.close()
