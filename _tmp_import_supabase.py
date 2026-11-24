import sqlite3
import supabase_connect
from dotenv import load_dotenv
load_dotenv(".env")

def run_batches(insert_prefix, conflict_sql, cols, rows, label, batch_size=500):
    total = len(rows)
    done = 0
    for i in range(0, total, batch_size):
        batch = rows[i:i+batch_size]
        placeholders = ",".join(["(" + ",".join(["%s"]*cols) + ")"] * len(batch))
        sql = f"{insert_prefix} VALUES {placeholders} {conflict_sql}"
        params = [v for row in batch for v in row]
        attempts = 0
        while True:
            attempts += 1
            try:
                conn = supabase_connect.get_connection()
                cur = conn.cursor()
                try:
                    cur.execute(sql, params)
                    conn.commit()
                finally:
                    try: cur.close()
                    except Exception: pass
                    conn.close()
                done += len(batch)
                print(f"{label} {done}/{total}", flush=True)
                break
            except Exception as e:
                print(f"{label} retry {attempts} at {done}: {e}", flush=True)
                if attempts >= 5:
                    raise
    return total

print("driver", getattr(supabase_connect, "_DRIVER", None), flush=True)
sqlite_conn = sqlite3.connect("passes.db")
sqlite_conn.row_factory = sqlite3.Row
sc = sqlite_conn.cursor()
passes = sc.execute("SELECT serial,code,used,used_at,gate,revoked FROM passes ORDER BY serial").fetchall()
scan_log = sc.execute("SELECT id,ts,code,result,gate FROM scan_log ORDER BY id").fetchall()
audit = sc.execute("SELECT id,ts,action,code,detail FROM admin_audit ORDER BY id").fetchall()

p = run_batches("INSERT INTO passes (serial,code,used,used_at,gate,revoked)", "ON CONFLICT (serial) DO UPDATE SET code=EXCLUDED.code, used=EXCLUDED.used, used_at=EXCLUDED.used_at, gate=EXCLUDED.gate, revoked=EXCLUDED.revoked", 6, [(r["serial"],r["code"],r["used"],r["used_at"],r["gate"],r["revoked"]) for r in passes], "passes")
s = run_batches("INSERT INTO scan_log (id,ts,code,result,gate)", "ON CONFLICT (id) DO UPDATE SET ts=EXCLUDED.ts, code=EXCLUDED.code, result=EXCLUDED.result, gate=EXCLUDED.gate", 5, [(r["id"],r["ts"],r["code"],r["result"],r["gate"]) for r in scan_log], "scan_log")
a = run_batches("INSERT INTO admin_audit (id,ts,action,code,detail)", "ON CONFLICT (id) DO UPDATE SET ts=EXCLUDED.ts, action=EXCLUDED.action, code=EXCLUDED.code, detail=EXCLUDED.detail", 5, [(r["id"],r["ts"],r["action"],r["code"],r["detail"]) for r in audit], "admin_audit")
print("imported", p, s, a, flush=True)

with supabase_connect.db_cursor() as cur:
    for stmt in [
        "SELECT setval(pg_get_serial_sequence('passes','serial'), COALESCE((SELECT MAX(serial) FROM passes), 1), true)",
        "SELECT setval(pg_get_serial_sequence('scan_log','id'), COALESCE((SELECT MAX(id) FROM scan_log), 1), true)",
        "SELECT setval(pg_get_serial_sequence('admin_audit','id'), COALESCE((SELECT MAX(id) FROM admin_audit), 1), true)",
    ]:
        try: cur.execute(stmt)
        except Exception as e: print("seq warn", e, flush=True)
    for t in ["passes","scan_log","admin_audit"]:
        cur.execute(f"SELECT COUNT(*) FROM {t}")
        print(t, cur.fetchone(), flush=True)
    cur.execute("SELECT COUNT(*) AS total, COALESCE(SUM(CASE WHEN used=1 AND revoked=0 THEN 1 ELSE 0 END),0) AS entered FROM passes")
    print("summary", cur.fetchone(), flush=True)
sqlite_conn.close()
print("DONE", flush=True)
