"""SQLite helpers for Gopher Boys CRM."""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from werkzeug.security import generate_password_hash

_DEFAULT_DB = Path(__file__).resolve().parent / "data" / "gopher.db"
DB_PATH = Path(os.environ.get("DATABASE_PATH", str(_DEFAULT_DB)))

STATUSES = ("request", "lead", "quoted", "scheduled", "delivered", "invoiced", "cancelled")
ROLES = ("admin", "staff")


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def db():
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r["name"] for r in rows}


def init_db() -> None:
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'staff',
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS drivers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                phone TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                notes TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS contractors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_name TEXT NOT NULL,
                contact_name TEXT,
                phone TEXT,
                email TEXT,
                notes TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contractor_id INTEGER NOT NULL,
                supplier TEXT,
                order_ref TEXT,
                job_address TEXT,
                city TEXT,
                miles REAL,
                quote_amount REAL,
                wait_minutes_extra REAL DEFAULT 0,
                wait_fee REAL DEFAULT 0,
                extra_stops_notes TEXT,
                status TEXT NOT NULL DEFAULT 'lead',
                scheduled_at TEXT,
                delivered_at TEXT,
                notes TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (contractor_id) REFERENCES contractors(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS activity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL,
                user_id INTEGER,
                FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
            );


            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contractor_id INTEGER NOT NULL,
                job_id INTEGER,
                channel TEXT NOT NULL,
                to_address TEXT NOT NULL,
                subject TEXT,
                body TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'draft',
                created_by INTEGER,
                created_at TEXT NOT NULL,
                sent_at TEXT,
                FOREIGN KEY (contractor_id) REFERENCES contractors(id) ON DELETE CASCADE,
                FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE SET NULL,
                FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
            );

            CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
            CREATE INDEX IF NOT EXISTS idx_jobs_contractor ON jobs(contractor_id);
            CREATE INDEX IF NOT EXISTS idx_activity_job ON activity(job_id);
            CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
            CREATE INDEX IF NOT EXISTS idx_messages_status ON messages(status);
            CREATE INDEX IF NOT EXISTS idx_messages_contractor ON messages(contractor_id);
            CREATE INDEX IF NOT EXISTS idx_drivers_active ON drivers(is_active);

            CREATE TABLE IF NOT EXISTS geocode_cache (
                query TEXT PRIMARY KEY,
                lat REAL NOT NULL,
                lon REAL NOT NULL,
                source TEXT,
                created_at TEXT NOT NULL
            );
            """
        )

        # Migrate activity.user_id if table existed without it
        activity_cols = _column_names(conn, "activity")
        if "user_id" not in activity_cols:
            conn.execute("ALTER TABLE activity ADD COLUMN user_id INTEGER")

        # Migrate jobs.driver_id / materials if missing
        job_cols = _column_names(conn, "jobs")
        if "driver_id" not in job_cols:
            conn.execute("ALTER TABLE jobs ADD COLUMN driver_id INTEGER")
        if "materials" not in job_cols:
            conn.execute("ALTER TABLE jobs ADD COLUMN materials TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_driver ON jobs(driver_id)"
        )

        # Seed contractor if empty
        row = conn.execute("SELECT COUNT(*) AS c FROM contractors").fetchone()
        if row["c"] == 0:
            now = utcnow()
            conn.execute(
                """
                INSERT INTO contractors
                    (company_name, contact_name, phone, email, notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "Ridgeway Roofing Co.",
                    "Mack Ridgeway",
                    "(912) 777-2339",
                    "",
                    "Gopher Boys operator / pickup contact. Seeded from flyer.",
                    now,
                ),
            )

        # Seed default users if none exist
        user_count = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
        if user_count == 0:
            seed_default_users(conn)


def seed_default_users(conn: sqlite3.Connection) -> None:
    """Create Chris (admin) and Mack (staff) on first boot."""
    password = os.environ.get("ADMIN_PASSWORD") or "ChangeMeNow!"
    pw_hash = generate_password_hash(password)
    now = utcnow()
    seeds = [
        ("chris@ridgewayroofingco.com", "Chris Vincent", "admin"),
        ("mack@ridgewayroofingco.com", "Mack Ridgeway", "staff"),
    ]
    for email, name, role in seeds:
        conn.execute(
            """
            INSERT INTO users (email, name, password_hash, role, is_active, created_at)
            VALUES (?, ?, ?, ?, 1, ?)
            """,
            (email, name, pw_hash, role, now),
        )


def add_activity(
    conn: sqlite3.Connection,
    job_id: int,
    message: str,
    user_id: int | None = None,
) -> None:
    conn.execute(
        "INSERT INTO activity (job_id, message, created_at, user_id) VALUES (?, ?, ?, ?)",
        (job_id, message, utcnow(), user_id),
    )


def get_user_by_id(user_id: int):
    with db() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()


def get_user_by_email(email: str):
    with db() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE email = ? COLLATE NOCASE", (email,)
        ).fetchone()
