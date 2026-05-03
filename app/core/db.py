"""
PostgreSQL-backed job store.

Uses psycopg2 (sync) for all DB operations — consistent with the synchronous
nature of background tasks and SDK calls throughout the project.

Connection string read from DATABASE_URL in .env:
    DATABASE_URL=postgresql://dahua:password@localhost:5432/dahua_api

Install:
    pip install psycopg2-binary

Create the database first (run once in psql):
    CREATE DATABASE dahua_api;
    CREATE USER dahua WITH PASSWORD 'your_password';
    GRANT ALL PRIVILEGES ON DATABASE dahua_api TO dahua;
    -- In psql connected to dahua_api:
    GRANT ALL ON SCHEMA public TO dahua;
"""
import json
import logging
from contextlib import contextmanager
from datetime import datetime
from typing import Optional

import psycopg2
import psycopg2.extras

from app.config import get_settings

log = logging.getLogger(__name__)


def _get_dsn() -> str:
    url = get_settings().database_url
    if not url.startswith("postgresql"):
        raise ValueError(
            f"DATABASE_URL must start with 'postgresql://'. Got: {url}\n"
            "Example: DATABASE_URL=postgresql://dahua:password@localhost:5432/dahua_api"
        )
    return url


@contextmanager
def get_conn():
    """Context manager: yields a psycopg2 connection, commits on success, rolls back on error."""
    conn = psycopg2.connect(_get_dsn())
    conn.autocommit = False
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Create all tables if they don't exist. Run once at startup."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id          TEXT PRIMARY KEY,
                    kind        TEXT        NOT NULL,
                    status      TEXT        NOT NULL,
                    progress    TEXT,
                    params_json TEXT,
                    result_json TEXT,
                    error       TEXT,
                    created_at  TEXT        NOT NULL,
                    updated_at  TEXT        NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_jobs_status
                    ON jobs(status);

                CREATE INDEX IF NOT EXISTS idx_jobs_created
                    ON jobs(created_at);

                CREATE TABLE IF NOT EXISTS dghs_state (
                    device              TEXT PRIMARY KEY,
                    dghs_device_id      TEXT,
                    last_pushed         TEXT,
                    last_pushed_count   INTEGER,
                    last_run_at         TEXT
                );
            """)
    log.info("db_init_ok")


# ---------------------------------------------------------------------------
# Job operations
# ---------------------------------------------------------------------------
def insert_job(job_id: str, kind: str, params: dict, status: str = "queued"):
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO jobs "
                "(id, kind, status, params_json, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (job_id, kind, status, json.dumps(params), now, now),
            )


def update_job(job_id: str, **fields):
    fields["updated_at"] = datetime.utcnow().isoformat()
    cols = ", ".join(f"{k} = %s" for k in fields)
    vals = list(fields.values()) + [job_id]
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE jobs SET {cols} WHERE id = %s", vals)


def get_job(job_id: str) -> Optional[dict]:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM jobs WHERE id = %s", (job_id,))
            row = cur.fetchone()
            return dict(row) if row else None


def list_jobs(limit: int = 50) -> list[dict]:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT %s", (limit,)
            )
            return [dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# DGHS state operations
# (Moved here from dghs_service.py so all DB logic lives in one file)
# ---------------------------------------------------------------------------
def get_dghs_state(device: str) -> Optional[dict]:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM dghs_state WHERE device = %s", (device,)
            )
            row = cur.fetchone()
            return dict(row) if row else None


def upsert_dghs_state(device: str, dghs_device_id: str,
                      last_pushed: Optional[str], count: int):
    now = datetime.utcnow().isoformat(timespec="seconds")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO dghs_state
                    (device, dghs_device_id, last_pushed, last_pushed_count, last_run_at)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (device) DO UPDATE SET
                    dghs_device_id    = EXCLUDED.dghs_device_id,
                    last_pushed       = COALESCE(EXCLUDED.last_pushed,
                                                 dghs_state.last_pushed),
                    last_pushed_count = EXCLUDED.last_pushed_count,
                    last_run_at       = EXCLUDED.last_run_at
            """, (device, dghs_device_id, last_pushed, count, now))


def delete_dghs_state(device: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM dghs_state WHERE device = %s", (device,)
            )
