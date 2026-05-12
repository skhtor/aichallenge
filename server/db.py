"""Shared database setup and helpers — PostgreSQL with connection pooling."""
import os
from contextlib import contextmanager

import psycopg2
import psycopg2.pool
import psycopg2.extras

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://ants:ants@localhost:5432/ants")

_pool = None


def _get_pool():
    global _pool
    if _pool is None:
        _pool = psycopg2.pool.ThreadedConnectionPool(2, 20, DATABASE_URL)
    return _pool


def get_db():
    """Get a connection from the pool. Caller must call conn.close() to return it."""
    conn = _get_pool().getconn()
    conn.autocommit = False
    return conn


def release_db(conn):
    """Return a connection to the pool."""
    _get_pool().putconn(conn)


@contextmanager
def db_conn():
    """Context manager for DB connections — auto-commits on success, rolls back on error."""
    conn = get_db()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        release_db(conn)


@contextmanager
def db_readonly():
    """Read-only connection context manager."""
    conn = get_db()
    conn.autocommit = True
    try:
        yield conn
    finally:
        release_db(conn)


def dict_cursor(conn):
    """Get a cursor that returns dicts."""
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


def init_db():
    """Create tables if they don't exist."""
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS teams (
                id SERIAL PRIMARY KEY,
                name TEXT UNIQUE NOT NULL,
                token TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS bots (
                id SERIAL PRIMARY KEY,
                team_id INTEGER NOT NULL REFERENCES teams(id),
                name TEXT UNIQUE NOT NULL,
                language TEXT DEFAULT 'python',
                elo DOUBLE PRECISION DEFAULT 1500,
                rd DOUBLE PRECISION DEFAULT 350,
                volatility DOUBLE PRECISION DEFAULT 0.06,
                games_played INTEGER DEFAULT 0,
                wins INTEGER DEFAULT 0,
                active_version INTEGER,
                active INTEGER DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS bot_versions (
                id SERIAL PRIMARY KEY,
                bot_id INTEGER NOT NULL REFERENCES bots(id),
                version INTEGER NOT NULL,
                uploaded_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS matches (
                id SERIAL PRIMARY KEY,
                played_at TEXT NOT NULL,
                map_file TEXT NOT NULL,
                turns INTEGER,
                replay_file TEXT
            );
            CREATE TABLE IF NOT EXISTS match_players (
                match_id INTEGER NOT NULL REFERENCES matches(id),
                bot_id INTEGER NOT NULL REFERENCES bots(id),
                bot_version INTEGER,
                player_index INTEGER,
                score INTEGER,
                status TEXT,
                elo_change DOUBLE PRECISION
            );
            CREATE TABLE IF NOT EXISTS elo_history (
                id SERIAL PRIMARY KEY,
                bot_id INTEGER NOT NULL REFERENCES bots(id),
                match_id INTEGER NOT NULL REFERENCES matches(id),
                elo DOUBLE PRECISION NOT NULL,
                recorded_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_match_players_match_id ON match_players(match_id);
            CREATE INDEX IF NOT EXISTS idx_match_players_bot_id ON match_players(bot_id);
            CREATE INDEX IF NOT EXISTS idx_elo_history_bot_id ON elo_history(bot_id);
            CREATE INDEX IF NOT EXISTS idx_matches_id_desc ON matches(id DESC);
        """)
        # Migration: add rd and volatility columns if they don't exist
        cur.execute("""
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='bots' AND column_name='rd') THEN
                    ALTER TABLE bots ADD COLUMN rd DOUBLE PRECISION DEFAULT 350;
                END IF;
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='bots' AND column_name='volatility') THEN
                    ALTER TABLE bots ADD COLUMN volatility DOUBLE PRECISION DEFAULT 0.06;
                END IF;
                ALTER TABLE bots ALTER COLUMN elo SET DEFAULT 1500;
            END $$;
        """)
