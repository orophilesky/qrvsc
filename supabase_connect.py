"""Supabase Postgres connection helpers for EventPass.

This project currently uses SQLite (`sqlite3`) in app.py/generate_qr.py.
Use this module when migrating to Supabase Postgres.

Setup:
  1. In Supabase Dashboard -> Connect -> copy the Postgres URI (direct or pooler).
  2. Set env var SUPABASE_DB_URL (or DATABASE_URL), e.g. in .env:
       SUPABASE_DB_URL=postgresql://.../postgres?sslmode=require
  3. Install dependencies:
       pip install "psycopg[binary]>=3.1" python-dotenv
     If psycopg binary is unavailable/blocked on your system, install pg8000:
       pip install pg8000 python-dotenv

Notes for migrating from SQLite:
  - Placeholders: SQLite uses `?`, Postgres drivers commonly use `%s`.
  - Row access: sqlite3.Row allows name/index access; prefer dict-like rows.
  - Autoincrement: INTEGER PRIMARY KEY AUTOINCREMENT -> BIGSERIAL PRIMARY KEY.
  - Transactions/locking: replace BEGIN IMMEDIATE with Postgres-safe atomic
    updates / SELECT ... FOR UPDATE as needed.
  - PRAGMA/busy_timeout/sqlite3-specific error handling must be replaced.
"""

from __future__ import annotations

import os
import ssl
import urllib.parse
from contextlib import contextmanager
from typing import Iterator

try:
    import psycopg
    from psycopg.rows import dict_row

    _DRIVER = "psycopg"
except Exception:  # pragma: no cover - fallback for environments blocking psycopg binary
    psycopg = None
    dict_row = None
    import pg8000.dbapi

    _DRIVER = "pg8000"


def get_database_url() -> str:
    """Return Supabase/Postgres connection URL from environment."""
    url = (
        os.environ.get("SUPABASE_DB_URL")
        or os.environ.get("DATABASE_URL")
        or os.environ.get("SUPABASE_POSTGRES_URL")
    )
    if not url:
        raise RuntimeError(
            "Set SUPABASE_DB_URL (or DATABASE_URL) to your Supabase Postgres connection string."
        )
    return url


def _pg8000_connect(url: str):
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query)
    sslmode = (query.get("sslmode", ["require"])[0] or "require").lower()
    ssl_context = None
    if sslmode not in ("disable", "allow"):
        ssl_context = ssl.create_default_context()
        if os.environ.get("SUPABASE_DB_SSL_INSECURE") == "1":
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
    try:
        return pg8000.dbapi.connect(
            user=urllib.parse.unquote(parsed.username or ""),
            password=urllib.parse.unquote(parsed.password or ""),
            host=parsed.hostname,
            port=parsed.port or 5432,
            database=(parsed.path or "/postgres").lstrip("/") or "postgres",
            ssl_context=ssl_context,
        )
    except ssl.SSLCertVerificationError:
        if ssl_context is None or os.environ.get("SUPABASE_DB_SSL_INSECURE") == "1":
            raise
        insecure = ssl.create_default_context()
        insecure.check_hostname = False
        insecure.verify_mode = ssl.CERT_NONE
        print("Warning: SSL certificate verification failed; retrying with verification disabled.")
        print("Set proper CA trust for production, or SUPABASE_DB_SSL_INSECURE=1 to silence this fallback.")
        return pg8000.dbapi.connect(
            user=urllib.parse.unquote(parsed.username or ""),
            password=urllib.parse.unquote(parsed.password or ""),
            host=parsed.hostname,
            port=parsed.port or 5432,
            database=(parsed.path or "/postgres").lstrip("/") or "postgres",
            ssl_context=insecure,
        )


def get_connection():
    """Create a new Postgres connection."""
    url = get_database_url()
    if _DRIVER == "psycopg":
        return psycopg.connect(url, row_factory=dict_row)
    return _pg8000_connect(url)


@contextmanager
def db_cursor(commit: bool = False) -> Iterator:
    """Yield a cursor; commit on success when commit=True, else rollback/close."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        yield cur
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        try:
            cur.close()
        finally:
            conn.close()


SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS passes (
        serial BIGSERIAL PRIMARY KEY,
        code TEXT UNIQUE NOT NULL,
        used INTEGER NOT NULL DEFAULT 0,
        used_at TEXT,
        gate TEXT,
        revoked INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS scan_log (
        id BIGSERIAL PRIMARY KEY,
        ts TEXT NOT NULL,
        code TEXT NOT NULL,
        result TEXT NOT NULL,
        gate TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS admin_audit (
        id BIGSERIAL PRIMARY KEY,
        ts TEXT NOT NULL,
        action TEXT NOT NULL,
        code TEXT NOT NULL,
        detail TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS scan_log_ts_idx ON scan_log(ts)",
    "CREATE INDEX IF NOT EXISTS scan_log_gate_idx ON scan_log(gate)",
]


def _fetch_version(cur):
    row = cur.fetchone()
    if row is None:
        return None
    if isinstance(row, dict):
        return row.get("version")
    return row[0]


def init_db() -> None:
    """Create EventPass tables in Supabase/Postgres if they do not exist."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        for stmt in SCHEMA_STATEMENTS:
            cur.execute(stmt)
        conn.commit()
    finally:
        try:
            cur.close()
        finally:
            conn.close()


if __name__ == "__main__":
    # Quick connectivity test:
    #   set SUPABASE_DB_URL=postgresql://...  (or put in .env)
    #   python supabase_connect.py
    try:
        from dotenv import load_dotenv  # optional

        load_dotenv(".env")
    except Exception:
        pass

    with db_cursor() as cur:
        cur.execute("SELECT version();")
        print("Connected to:", _fetch_version(cur))
        try:
            cur.execute("SELECT current_database(), current_user")
            row = cur.fetchone()
            if isinstance(row, dict):
                print("Database/user:", row.get("current_database"), row.get("current_user"))
            else:
                print("Database/user:", row)
        except Exception:
            pass
    print(f"Driver: {_DRIVER}")

