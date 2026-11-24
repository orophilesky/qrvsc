#!/usr/bin/env python3
"""One-shot Supabase setup for EventPass.

What this script does (automatable parts):
  1. Loads SUPABASE_DB_URL (or DATABASE_URL) from .env/environment.
  2. Tests connection to Supabase Postgres.
  3. Creates required tables/indexes if missing.
  4. Imports data from local SQLite passes.db into Supabase:
     - passes, scan_log, admin_audit (upsert/idempotent)
  5. Resets Postgres sequences and prints verification counts.

Prerequisites:
  - Set SUPABASE_DB_URL in .env (gitignored) or environment.
  - Install drivers:
      py -3 -m pip install pg8000 python-dotenv
    (psycopg[binary] also supported if available)

Run:
  py -3 setup_supabase_all.py
Optional:
  py -3 setup_supabase_all.py --sqlite passes.db
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys

try:
    from dotenv import load_dotenv

    load_dotenv(".env")
except Exception:
    pass

import supabase_connect


def _value(row, key, index=None):
    if isinstance(row, dict):
        return row.get(key)
    if index is not None:
        return row[index]
    return row[key]


EXPECTED_SCHEMAS = {
    "passes": ["serial", "code", "used", "used_at", "gate", "revoked"],
    "scan_log": ["id", "ts", "code", "result", "gate"],
    "admin_audit": ["id", "ts", "action", "code", "detail"],
}


def _sqlite_conn(path: str):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _pg_execute(cur, sql: str, params=None):
    cur.execute(sql, params)


def _table_columns(pg_cur, table: str):
    pg_cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema='public' AND table_name=%s
        ORDER BY ordinal_position
        """,
        (table,),
    )
    cols = []
    for row in pg_cur.fetchall():
        cols.append(row.get("column_name") if isinstance(row, dict) else row[0])
    return cols


def ensure_compatible_schema(pg_conn):
    pg_cur = pg_conn.cursor()
    try:
        # Ensure scan_log/admin_audit exist (and other schema objects) first.
        for stmt in supabase_connect.SCHEMA_STATEMENTS:
            # Skip passes creation here; we handle passes explicitly for mismatch cases.
            if "CREATE TABLE IF NOT EXISTS passes" in stmt:
                continue
            pg_cur.execute(stmt)
        pg_conn.commit()

        actual = _table_columns(pg_cur, "passes")
        expected = EXPECTED_SCHEMAS["passes"]
        if actual and actual != expected:
            print(f"Detected incompatible 'passes' schema in Supabase: {actual}")
            print(f"Recreating 'passes' to match app schema: {expected}")
            pg_cur.execute("DROP TABLE IF EXISTS passes")
            actual = []
        if not actual:
            pg_cur.execute(
                """
                CREATE TABLE passes (
                    serial BIGSERIAL PRIMARY KEY,
                    code TEXT UNIQUE NOT NULL,
                    used INTEGER NOT NULL DEFAULT 0,
                    used_at TEXT,
                    gate TEXT,
                    revoked INTEGER NOT NULL DEFAULT 0
                )
                """
            )
        # Ensure all schema statements (safe IF NOT EXISTS) are applied.
        for stmt in supabase_connect.SCHEMA_STATEMENTS:
            pg_cur.execute(stmt)
        pg_conn.commit()
    finally:
        pg_cur.close()


def _pg_executemany(cur, sql: str, seq):
    # pg8000 and psycopg both support executemany; fallback to loop if needed.
    try:
        cur.executemany(sql, seq)
    except Exception:
        for item in seq:
            cur.execute(sql, item)


def migrate_passes(sqlite_cur, pg_cur) -> int:
    rows = sqlite_cur.execute(
        "SELECT serial, code, used, used_at, gate, revoked FROM passes ORDER BY serial"
    ).fetchall()
    if not rows:
        return 0
    sql = """
        INSERT INTO passes (serial, code, used, used_at, gate, revoked)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (serial) DO UPDATE SET
            code = EXCLUDED.code,
            used = EXCLUDED.used,
            used_at = EXCLUDED.used_at,
            gate = EXCLUDED.gate,
            revoked = EXCLUDED.revoked
    """
    data = [
        (r["serial"], r["code"], r["used"], r["used_at"], r["gate"], r["revoked"]) for r in rows
    ]
    batch = 1000
    for i in range(0, len(data), batch):
        _pg_executemany(pg_cur, sql, data[i : i + batch])
    return len(rows)


def migrate_scan_log(sqlite_cur, pg_cur) -> int:
    rows = sqlite_cur.execute(
        "SELECT id, ts, code, result, gate FROM scan_log ORDER BY id"
    ).fetchall()
    if not rows:
        return 0
    sql = """
        INSERT INTO scan_log (id, ts, code, result, gate)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (id) DO UPDATE SET
            ts = EXCLUDED.ts,
            code = EXCLUDED.code,
            result = EXCLUDED.result,
            gate = EXCLUDED.gate
    """
    data = [(r["id"], r["ts"], r["code"], r["result"], r["gate"]) for r in rows]
    _pg_executemany(pg_cur, sql, data)
    return len(rows)


def migrate_admin_audit(sqlite_cur, pg_cur) -> int:
    rows = sqlite_cur.execute(
        "SELECT id, ts, action, code, detail FROM admin_audit ORDER BY id"
    ).fetchall()
    if not rows:
        return 0
    sql = """
        INSERT INTO admin_audit (id, ts, action, code, detail)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (id) DO UPDATE SET
            ts = EXCLUDED.ts,
            action = EXCLUDED.action,
            code = EXCLUDED.code,
            detail = EXCLUDED.detail
    """
    data = [(r["id"], r["ts"], r["action"], r["code"], r["detail"]) for r in rows]
    _pg_executemany(pg_cur, sql, data)
    return len(rows)


def reset_sequences(pg_cur):
    stmts = [
        "SELECT setval(pg_get_serial_sequence('passes','serial'), COALESCE((SELECT MAX(serial) FROM passes), 1), true)",
        "SELECT setval(pg_get_serial_sequence('scan_log','id'), COALESCE((SELECT MAX(id) FROM scan_log), 1), true)",
        "SELECT setval(pg_get_serial_sequence('admin_audit','id'), COALESCE((SELECT MAX(id) FROM admin_audit), 1), true)",
    ]
    for stmt in stmts:
        try:
            pg_cur.execute(stmt)
        except Exception:
            pass


def fetch_count(cur, table: str):
    cur.execute(f"SELECT COUNT(*) AS c FROM {table}")
    row = cur.fetchone()
    if isinstance(row, dict):
        return row.get("c")
    return row[0]


def main():
    parser = argparse.ArgumentParser(description="Setup and migrate EventPass data to Supabase Postgres.")
    parser.add_argument("--sqlite", default=os.environ.get("DB_PATH", "passes.db"), help="Path to local SQLite DB (default: passes.db)")
    args = parser.parse_args()

    if not os.path.exists(args.sqlite):
        print(f"ERROR: SQLite DB not found: {args.sqlite}")
        sys.exit(1)

    print(f"Driver: {getattr(supabase_connect, '_DRIVER', 'unknown')}")
    print("Testing connection...")
    with supabase_connect.db_cursor() as cur:
        cur.execute("SELECT version()")
        row = cur.fetchone()
        version = row.get("version") if isinstance(row, dict) else row[0]
        print("Connected:", version)

    print("Creating/verifying tables and indexes...")
    schema_conn = supabase_connect.get_connection()
    try:
        ensure_compatible_schema(schema_conn)
    finally:
        schema_conn.close()

    sqlite_conn = _sqlite_conn(args.sqlite)
    sqlite_cur = sqlite_conn.cursor()

    pg_conn = supabase_connect.get_connection()
    pg_cur = pg_conn.cursor()
    try:
        print(f"Importing from {args.sqlite} ...")
        p = migrate_passes(sqlite_cur, pg_cur)
        s = migrate_scan_log(sqlite_cur, pg_cur)
        a = migrate_admin_audit(sqlite_cur, pg_cur)
        reset_sequences(pg_cur)
        pg_conn.commit()
        print(f"Imported: passes={p}, scan_log={s}, admin_audit={a}")

        print("Verification (Supabase):")
        for table in ("passes", "scan_log", "admin_audit"):
            try:
                print(f"  {table}: {fetch_count(pg_cur, table)}")
            except Exception as e:
                print(f"  {table}: error {e}")
        try:
            pg_cur.execute(
                "SELECT COUNT(*) AS total, "
                "COALESCE(SUM(CASE WHEN used=1 AND revoked=0 THEN 1 ELSE 0 END),0) AS entered "
                "FROM passes"
            )
            row = pg_cur.fetchone()
            if isinstance(row, dict):
                print(f"  passes summary: total={row.get('total')} entered={row.get('entered')}")
            else:
                print(f"  passes summary: total={row[0]} entered={row[1]}")
        except Exception:
            pass
    except Exception:
        pg_conn.rollback()
        raise
    finally:
        try:
            pg_cur.close()
        finally:
            pg_conn.close()
        sqlite_conn.close()

    print("Done. Supabase DB setup/import completed.")
    print("Note: The Flask app (app.py) currently uses SQLite. To run against Supabase, app code must be migrated to Postgres.")


if __name__ == "__main__":
    main()