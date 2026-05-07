import secrets
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "turing.db"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def connection():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """Add columns/tables for existing DBs."""
    sess_cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()}
    if "guess_unlock_at" not in sess_cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN guess_unlock_at TEXT")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS turns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (session_id) REFERENCES sessions(id)
        )
        """
    )

    msg_cols = {r[1] for r in conn.execute("PRAGMA table_info(messages)").fetchall()}
    if "turn_id" not in msg_cols:
        conn.execute("ALTER TABLE messages ADD COLUMN turn_id INTEGER REFERENCES turns(id)")

    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_one_responder_per_turn_channel
        ON messages(session_id, turn_id, channel)
        WHERE role = 'responder' AND turn_id IS NOT NULL
        """
    )

    if "calibration_key" not in sess_cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN calibration_key TEXT")

    if "guesser_token" not in sess_cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN guesser_token TEXT")

    turns_cols = {r[1] for r in conn.execute("PRAGMA table_info(turns)").fetchall()}
    if "llm_failed" not in turns_cols:
        conn.execute("ALTER TABLE turns ADD COLUMN llm_failed INTEGER NOT NULL DEFAULT 0")

    guess_cols = {r[1] for r in conn.execute("PRAGMA table_info(guesses)").fetchall()}
    if "was_correct" not in guess_cols:
        conn.execute("ALTER TABLE guesses ADD COLUMN was_correct INTEGER")

    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_guesser_token "
        "ON sessions(guesser_token) WHERE guesser_token IS NOT NULL"
    )

    for row in conn.execute(
        "SELECT id FROM sessions WHERE guesser_token IS NULL OR trim(guesser_token) = ''"
    ):
        conn.execute(
            "UPDATE sessions SET guesser_token = ? WHERE id = ?",
            (secrets.token_urlsafe(24), row["id"]),
        )


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connection() as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                ai_a INTEGER NOT NULL DEFAULT 0,
                ai_b INTEGER NOT NULL DEFAULT 0,
                join_token_a TEXT NOT NULL UNIQUE,
                join_token_b TEXT NOT NULL UNIQUE,
                reply_token_a TEXT NOT NULL,
                reply_token_b TEXT NOT NULL,
                guess_unlock_at TEXT,
                calibration_key TEXT
            );

            CREATE TABLE IF NOT EXISTS turns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                channel TEXT NOT NULL CHECK (channel IN ('A', 'B')),
                role TEXT NOT NULL CHECK (role IN ('guesser', 'responder')),
                content TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                turn_id INTEGER,
                FOREIGN KEY (session_id) REFERENCES sessions(id),
                FOREIGN KEY (turn_id) REFERENCES turns(id)
            );

            CREATE INDEX IF NOT EXISTS idx_messages_session_channel
            ON messages(session_id, channel, id);

            CREATE TABLE IF NOT EXISTS guesses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                choice TEXT NOT NULL CHECK (choice IN ('A', 'B')),
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                was_correct INTEGER,
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            );
            """
        )
        _migrate_schema(conn)
