#!/usr/bin/env python3
"""Flask check-in service for EventPass."""

import csv
import io
import os
import shutil
import sqlite3
import ssl
import threading
import time
import urllib.parse
from collections import defaultdict, deque
from datetime import datetime, timezone
from functools import wraps

from flask import Flask, Response, jsonify, render_template_string, request

try:
    import psycopg
    from psycopg.rows import dict_row

    _PG_DRIVER = "psycopg"
except Exception:  # pragma: no cover - fallback for environments without psycopg
    psycopg = None
    dict_row = None
    try:
        import pg8000.dbapi as pg8000_dbapi

        _PG_DRIVER = "pg8000"
    except Exception:  # pragma: no cover
        pg8000_dbapi = None
        _PG_DRIVER = None


SCANNER_PAGE = r"""<!doctype html>
<html lang="en">
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
  <meta name="theme-color" content="#101827">
  <title>EventPass Gate Scanner</title>
  <script src="https://unpkg.com/html5-qrcode@2.3.8/html5-qrcode.min.js"></script>
  <style>
    *{box-sizing:border-box}body{margin:0;background:#101827;color:#f8fafc;font:16px system-ui,sans-serif}
    main{max-width:620px;margin:auto;padding:18px}h1{font-size:1.55rem;margin:4px 0 16px}
    label{display:block;color:#b8c5d8;font-size:.88rem;margin:12px 0 5px}
    input,button{width:100%;padding:13px;border:1px solid #536176;border-radius:9px;font:inherit}
    input{background:#1b2638;color:#fff}button{background:#2563eb;color:#fff;border:0;font-weight:700;margin-top:12px}
    #counter{background:#1b2638;text-align:center;border-radius:10px;padding:13px;margin:16px 0;font-weight:700}
    #reader{width:100%;border:0!important;border-radius:12px;overflow:hidden;background:#fff}
    #reader video{object-fit:cover}#manual{display:flex;gap:8px}#manual input{min-width:0}
    #manual button{width:110px;margin:0}#status{color:#b8c5d8;margin-top:10px;font-size:.9rem}
    #overlay{display:none;position:fixed;inset:0;z-index:5;align-items:center;justify-content:center;text-align:center;padding:24px;color:#fff;font-size:clamp(2rem,10vw,4rem);font-weight:850}
    #overlay small{display:block;font-size:1.2rem;margin-top:15px;overflow-wrap:anywhere}
  </style>
</head>
<body><main>
  <h1>EventPass Gate Scanner</h1>
  <label for="gate">Gate name</label><input id="gate" autocomplete="off" placeholder="Gate A">
  <label for="pin">Staff PIN</label><input id="pin" inputmode="numeric" type="password" autocomplete="off" placeholder="Staff PIN">
  <div id="counter">Entered -- / -- | Remaining --</div>
  <div id="reader"></div>
  <label for="manualCode">Manual fallback</label>
  <div id="manual"><input id="manualCode" maxlength="12" autocomplete="off" placeholder="12-character code"><button id="submitCode">Scan code</button></div>
  <div id="status">Allow camera access to scan.</div>
</main><div id="overlay"></div>
<script>
(() => {
  const gate = document.getElementById('gate'), pin = document.getElementById('pin');
  const overlay = document.getElementById('overlay'), status = document.getElementById('status');
  const seen = new Map(); let showing = false, scanner = null;
  const color = {OK:'#15803d', USED:'#b91c1c', INVALID:'#b91c1c', ERROR:'#c2410c'};
  function tone(ok) {
    try { const audio = new (window.AudioContext || window.webkitAudioContext)();
      const oscillator = audio.createOscillator(), gain = audio.createGain();
      oscillator.frequency.value = ok ? 880 : 220; gain.gain.value = .08;
      oscillator.connect(gain); gain.connect(audio.destination); oscillator.start();
      oscillator.stop(audio.currentTime + (ok ? .12 : .3)); oscillator.onended = () => audio.close();
    } catch (_) {}
    if (navigator.vibrate) navigator.vibrate(ok ? [100,50,100] : [300]);
  }
  async function stats() {
    try { const response = await fetch('/api/stats'); if (!response.ok) return;
      const data = await response.json();
      if (typeof data.entered === 'number') {
        document.getElementById('counter').textContent =
          `Entered ${data.entered} / ${data.total} | Remaining ${data.remaining}`;
      }
    } catch (_) {}
  }
  async function scan(raw) {
    const code = raw.trim().toUpperCase(), now = Date.now();
    if (showing || !code || (seen.has(code) && now - seen.get(code) < 3000)) return;
    seen.set(code, now); showing = true;
    if (seen.size > 200) for (const [key, stamp] of seen) if (now - stamp >= 3000) seen.delete(key);
    try {
      const response = await fetch('/api/scan', {method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({code, gate:gate.value.trim(), pin:pin.value})});
      let data = null;
      try { data = await response.json(); } catch (_) {}
      if (!response.ok) {
        const message = (data && data.error) ? data.error : (response.status === 401 ? 'Wrong staff PIN' : `Request failed (${response.status})`);
        throw new Error(message);
      }
      if (!data || !data.status) throw new Error('Invalid server response');
      let message, success = false;
      if (data.status === 'OK') {message = `ENTRY OK<small>Pass ${data.serial}</small>`;success = true;}
      else if (data.status === 'USED') message = `ALREADY USED<small>${data.used_at || ''} · ${data.gate || 'Unknown gate'}</small>`;
      else message = 'INVALID PASS';
      overlay.innerHTML = message; overlay.style.background = color[data.status] || color.INVALID;
      status.textContent = data.status === 'OK' ? `Checked in: ${data.serial}` : data.status;
      tone(success); await stats();
    } catch (error) {
      const text = (error && error.name === 'TypeError') ? 'NETWORK ERROR, scan again' : (error.message || 'Scan failed');
      overlay.textContent = text;
      overlay.style.background = color.ERROR; tone(false);
      status.textContent = text;
    }
    overlay.style.display = 'flex';
    setTimeout(() => {overlay.style.display='none';showing=false;}, 2000);
  }
  document.getElementById('submitCode').addEventListener('click', () => {
    const input = document.getElementById('manualCode'); scan(input.value); input.value = '';
  });
  document.getElementById('manualCode').addEventListener('keydown', e => {
    if (e.key === 'Enter') {e.preventDefault();document.getElementById('submitCode').click();}
  });
  if (window.Html5Qrcode) {
    scanner = new Html5Qrcode('reader');
    scanner.start({facingMode:'environment'}, {fps:10,qrbox:{width:250,height:250}},
      text => scan(text), () => {}).then(() => status.textContent = 'Camera ready.')
      .catch(() => status.textContent = 'Camera unavailable. Use manual fallback or HTTPS.');
  } else status.textContent = 'Scanner library unavailable. Use manual fallback.';
  stats(); setInterval(stats, 15000);
})();
</script></body></html>"""


ADMIN_PAGE = r"""<!doctype html>
<html lang="en"><head><meta name="viewport" content="width=device-width,initial-scale=1">
<title>EventPass Admin</title><style>
body{font:15px system-ui,sans-serif;max-width:1100px;margin:24px auto;padding:0 16px;color:#172033}
input,button{padding:9px;margin:4px;border:1px solid #aaa;border-radius:5px}button{cursor:pointer}
table{border-collapse:collapse;width:100%;margin:12px 0}td,th{border:1px solid #ddd;padding:7px;text-align:left}
th{background:#f1f5f9}.scroll{overflow:auto}#message{font-weight:bold}
</style></head><body>
<h1>EventPass Admin</h1><label>Admin PIN <input id="pin" type="password" autocomplete="off"></label>
<button id="refresh">Load live report</button><button id="export">Export CSV</button>
<p id="message">Enter admin PIN to view reports.</p><div id="stats"></div><h2>Per-gate counts</h2><div id="gates"></div>
<h2>Search pass by code or serial</h2><input id="query" placeholder="Code or serial"><button id="search">Search</button>
<div id="searchResult"></div><h2>Recent scans (last 50)</h2><div class="scroll"><table><thead><tr>
<th>Time</th><th>Code</th><th>Result</th><th>Gate</th><th>Serial</th></tr></thead><tbody id="scans"></tbody></table></div>
<script>
const pin=()=>document.getElementById('pin').value;
async function post(url,body={}){const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({...body,pin:pin()})});const d=await r.json();if(!r.ok)throw Error(d.error||'Request failed');return d;}
async function refresh(){try{const d=await post('/admin/api/data');document.getElementById('message').textContent='Updated.';
document.getElementById('stats').textContent=`Total ${d.total} | Entered ${d.entered} | Remaining ${d.remaining}`;
document.getElementById('gates').textContent=d.gates.map(x=>`${x.gate||'(no gate)'}: ${x.count}`).join(' · ');
document.getElementById('scans').innerHTML=d.scans.map(x=>`<tr><td>${escapeHtml(x.ts)}</td><td>${escapeHtml(x.code)}</td>
<td>${escapeHtml(x.result)}</td><td>${escapeHtml(x.gate||'')}</td><td>${x.serial||''}</td></tr>`).join('');
}catch(e){document.getElementById('message').textContent=e.message;}}
function escapeHtml(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
document.getElementById('refresh').onclick=refresh;
document.getElementById('search').onclick=async()=>{try{const d=await post('/admin/api/search',{query:document.getElementById('query').value});
const p=d.pass;if(!p){document.getElementById('searchResult').textContent='No matching pass.';return;}
const box=document.getElementById('searchResult');box.textContent=`Serial ${p.serial} · ${p.code} · ${p.used?'USED':'UNUSED'} · ${p.gate||''} · Revoked ${!!p.revoked} `;
if(p.used){const b=document.createElement('button');b.textContent='Undo entry';b.onclick=()=>action('undo',p.code);box.appendChild(b);}
if(!p.revoked){const b=document.createElement('button');b.textContent='Revoke pass';b.onclick=()=>action('revoke',p.code);box.appendChild(b);}
}catch(e){document.getElementById('message').textContent=e.message;}};
async function action(action,code){if(!confirm(`${action} pass ${code}?`))return;try{await post('/admin/api/action',{action,code});await refresh();
document.getElementById('search').click();}catch(e){document.getElementById('message').textContent=e.message;}}
document.getElementById('export').onclick=async()=>{try{const r=await fetch('/admin/export.csv',{headers:{'X-Admin-Pin':pin()}});
if(!r.ok)throw Error('Export denied');const url=URL.createObjectURL(await r.blob()),a=document.createElement('a');
a.href=url;a.download='eventpass_checkins.csv';a.click();URL.revokeObjectURL(url);}catch(e){document.getElementById('message').textContent=e.message;}};
setInterval(()=>{if(pin())refresh();},15000);
</script></body></html>"""

class DbRow:
    def __init__(self, data):
        if isinstance(data, dict):
            self._data = dict(data)
            self._values = tuple(data.values())
        elif isinstance(data, DbRow):
            self._data = dict(data._data)
            self._values = tuple(data._values)
        else:
            seq = tuple(data)
            self._data = {}
            self._values = seq

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return self._data[key]

    def keys(self):
        return self._data.keys()

    def items(self):
        return self._data.items()

    def values(self):
        return self._data.values()

    def get(self, key, default=None):
        return self._data.get(key, default)

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)


class CursorWrapper:
    def __init__(self, cursor, use_postgres=False):
        self._cursor = cursor
        self._use_postgres = use_postgres

    @property
    def rowcount(self):
        return getattr(self._cursor, "rowcount", -1)

    @property
    def description(self):
        return self._cursor.description

    def _wrap(self, row):
        if row is None:
            return None
        if isinstance(row, DbRow):
            return row
        if isinstance(row, dict):
            return DbRow(row)
        desc = self._cursor.description
        if desc:
            data = {d[0]: row[i] for i, d in enumerate(desc)}
            return DbRow(data)
        return DbRow(row)

    def fetchone(self):
        return self._wrap(self._cursor.fetchone())

    def fetchall(self):
        return [self._wrap(r) for r in self._cursor.fetchall()]

    def __iter__(self):
        for row in self._cursor:
            yield self._wrap(row)

    def close(self):
        try:
            self._cursor.close()
        except Exception:
            pass


class DBConnection:
    def __init__(self, conn, use_postgres=False):
        self._conn = conn
        self._use_postgres = use_postgres

    def _adapt_sql(self, sql):
        if self._use_postgres:
            if sql.strip().upper() == "BEGIN IMMEDIATE":
                return None
            return sql.replace("?", "%s")
        return sql

    def execute(self, sql, params=None):
        sql = self._adapt_sql(sql)
        if sql is None:
            return CursorWrapper(self._conn.cursor(), self._use_postgres)
        cur = self._conn.cursor()
        if params is None:
            cur.execute(sql)
        else:
            cur.execute(sql, params)
        return CursorWrapper(cur, self._use_postgres)

    def executescript(self, script):
        if self._use_postgres:
            cur = self._conn.cursor()
            try:
                cur.execute(script)
            finally:
                cur.close()
            return
        return self._conn.executescript(script)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type:
                self._conn.rollback()
            else:
                self._conn.commit()
        finally:
            self._conn.close()
        return False


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _database_url():
    return (
        os.environ.get("SUPABASE_DB_URL")
        or os.environ.get("DATABASE_URL")
        or os.environ.get("SUPABASE_POSTGRES_URL")
    )


def _connect_postgres(url):
    if _PG_DRIVER == "psycopg":
        return psycopg.connect(url, row_factory=dict_row)
    if _PG_DRIVER == "pg8000":
        parsed = urllib.parse.urlparse(url)
        query = urllib.parse.parse_qs(parsed.query)
        sslmode = (query.get("sslmode", ["require"])[0] or "require").lower()
        ssl_context = None
        if sslmode not in ("disable", "allow"):
            ssl_context = ssl.create_default_context()
            if os.environ.get("SUPABASE_DB_SSL_INSECURE") == "1":
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE
        kwargs = dict(
            user=urllib.parse.unquote(parsed.username or ""),
            password=urllib.parse.unquote(parsed.password or ""),
            host=parsed.hostname,
            port=parsed.port or 5432,
            database=(parsed.path or "/postgres").lstrip("/") or "postgres",
            ssl_context=ssl_context,
        )
        try:
            return pg8000_dbapi.connect(**kwargs)
        except ssl.SSLCertVerificationError:
            if ssl_context is None or os.environ.get("SUPABASE_DB_SSL_INSECURE") == "1":
                raise
            insecure = ssl.create_default_context()
            insecure.check_hostname = False
            insecure.verify_mode = ssl.CERT_NONE
            return pg8000_dbapi.connect(**{**kwargs, "ssl_context": insecure})
    raise RuntimeError("No Postgres driver available. Install pg8000 or psycopg[binary].")


def get_sqlite_connection(db_path):
    connection = sqlite3.connect(db_path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=10000")
    return connection


POSTGRES_SCHEMA_STATEMENTS = [
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


def init_db(db_path=None, use_postgres=False, database_url=None):
    if use_postgres:
        conn = _connect_postgres(database_url or _database_url())
        cur = conn.cursor()
        try:
            cur.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema='public' AND table_name='passes'
                ORDER BY ordinal_position
                """
            )
            rows = cur.fetchall()
            cols = [r.get("column_name") if isinstance(r, dict) else r[0] for r in rows] if rows else []
            expected = ["serial", "code", "used", "used_at", "gate", "revoked"]
            if cols and cols != expected:
                cur.execute("DROP TABLE IF EXISTS passes")
            for stmt in POSTGRES_SCHEMA_STATEMENTS:
                cur.execute(stmt)
            conn.commit()
        finally:
            try:
                cur.close()
            finally:
                conn.close()
        return
    with get_sqlite_connection(db_path) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS passes (
                serial INTEGER PRIMARY KEY,
                code TEXT UNIQUE NOT NULL,
                used INTEGER DEFAULT 0,
                used_at TEXT,
                gate TEXT,
                revoked INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS scan_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                code TEXT NOT NULL,
                result TEXT NOT NULL,
                gate TEXT
            );
            CREATE TABLE IF NOT EXISTS admin_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                action TEXT NOT NULL,
                code TEXT NOT NULL,
                detail TEXT
            );
            CREATE INDEX IF NOT EXISTS scan_log_ts_idx ON scan_log(ts);
            CREATE INDEX IF NOT EXISTS scan_log_gate_idx ON scan_log(gate);
            """
        )


class ScanRateLimiter:
    def __init__(self, limit=10, period=1.0):
        self.limit = limit
        self.period = period
        self.requests = defaultdict(deque)
        self.lock = threading.Lock()

    def allow(self, ip, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            bucket = self.requests[ip]
            while bucket and now - bucket[0] >= self.period:
                bucket.popleft()
            if len(bucket) >= self.limit:
                return False
            bucket.append(now)
            return True


def _prepare_db_path(db_path):
    path = str(db_path)
    if not os.environ.get("VERCEL"):
        return path
    source = path
    if not os.path.isabs(source):
        source = os.path.abspath(source)
    target = os.path.join("/tmp", os.path.basename(source) or "passes.db")
    try:
        if os.path.exists(source) and not os.path.exists(target):
            shutil.copy2(source, target)
            for suffix in ("-wal", "-shm"):
                sidecar = source + suffix
                if os.path.exists(sidecar):
                    shutil.copy2(sidecar, target + suffix)
    except OSError:
        pass
    return target


def create_app(config=None):
    app = Flask(__name__)
    configured_db_path = os.environ.get("DB_PATH", "passes.db")
    database_url = _database_url()
    use_postgres = bool(database_url) or bool(config and config.get("USE_POSTGRES"))
    app.config.update(
        DB_PATH=_prepare_db_path(configured_db_path),
        DATABASE_URL=database_url,
        USE_POSTGRES=use_postgres,
        STAFF_PIN=os.environ.get("STAFF_PIN", ""),
        ADMIN_PIN=os.environ.get("ADMIN_PIN", ""),
    )
    if config:
        app.config.update(config)
        if "USE_POSTGRES" in config:
            use_postgres = bool(config["USE_POSTGRES"])
            app.config["USE_POSTGRES"] = use_postgres
        if config.get("DATABASE_URL"):
            app.config["DATABASE_URL"] = config["DATABASE_URL"]
    limiter = ScanRateLimiter(limit=30)

    try:
        init_db(app.config["DB_PATH"], use_postgres=app.config["USE_POSTGRES"], database_url=app.config.get("DATABASE_URL"))
    except Exception:
        if not app.config.get("TESTING"):
            app.logger.exception("Database initialization failed")

    def db():
        if app.config["USE_POSTGRES"]:
            return DBConnection(_connect_postgres(app.config.get("DATABASE_URL") or _database_url()), use_postgres=True)
        return DBConnection(get_sqlite_connection(app.config["DB_PATH"]), use_postgres=False)

    def json_db_error(exc):
        message = str(exc)
        low = message.lower()
        if "readonly" in low or "read-only" in low:
            return jsonify(error="Database is read-only on this deployment. Use a persistent host or an external database."), 500
        if "no such table" in low or "undefinedtable" in low or ("relation" in low and "does not exist" in low):
            return jsonify(error="Database is not initialized on this deployment."), 500
        return jsonify(error=f"Database error: {message}"), 500

    def authorized_admin():
        payload = request.get_json(silent=True) or {}
        provided = request.headers.get("X-Admin-Pin")
        if provided is None:
            provided = payload.get("pin")
        provided_pin = "" if provided is None else str(provided).strip()
        admin_pin = str(app.config["ADMIN_PIN"] or "").strip()
        return bool(admin_pin) and provided_pin == admin_pin

    def admin_required(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            if not authorized_admin():
                return jsonify(error="Unauthorized"), 401
            return function(*args, **kwargs)

        return wrapped

    @app.get("/")
    def scanner():
        return render_template_string(SCANNER_PAGE)

    @app.get("/api/stats")
    def stats():
        try:
            with db() as connection:
                total = connection.execute("SELECT COUNT(*) FROM passes").fetchone()[0]
                entered = connection.execute(
                    "SELECT COUNT(*) FROM passes WHERE used=1 AND revoked=0"
                ).fetchone()[0]
        except Exception as exc:
            return json_db_error(exc)
        return jsonify(total=int(total or 0), entered=int(entered or 0), remaining=int((total or 0) - (entered or 0)))

    @app.post("/api/scan")
    def scan():
        if not app.testing and not limiter.allow(request.remote_addr or "unknown"):
            return jsonify(error="Too many scans; retry shortly"), 429
        payload = request.get_json(silent=True) or {}
        code = str(payload.get("code") or "").strip().upper()
        gate = str(payload.get("gate") or "").strip()[:100]
        pin = payload.get("pin")
        provided_pin = "" if pin is None else str(pin).strip()
        staff_pin = str(app.config["STAFF_PIN"] or "").strip()
        if not staff_pin or provided_pin != staff_pin:
            try:
                with db() as connection:
                    connection.execute(
                        "INSERT INTO scan_log(ts,code,result,gate) VALUES (?,?,?,?)",
                        (utc_now(), code, "UNAUTHORIZED", gate),
                    )
            except Exception:
                pass
            return jsonify(error="Wrong staff PIN"), 401
        now = utc_now()
        try:
            with db() as connection:
                if app.config["USE_POSTGRES"]:
                    claimed = connection.execute(
                        """UPDATE passes SET used=1, used_at=?, gate=?
                           WHERE code=? AND used=0 AND revoked=0
                           RETURNING serial""",
                        (now, gate, code),
                    ).fetchone()
                    if claimed is not None:
                        result = {"status": "OK", "serial": int(claimed["serial"])}
                        log_result = "OK"
                    else:
                        row = connection.execute(
                            "SELECT serial,used,used_at,gate,revoked FROM passes WHERE code=?",
                            (code,),
                        ).fetchone()
                        if row is None or row["revoked"]:
                            result = {"status": "INVALID"}
                            log_result = "INVALID"
                        else:
                            result = {
                                "status": "USED",
                                "serial": int(row["serial"]),
                                "used_at": row["used_at"],
                                "gate": row["gate"],
                            }
                            log_result = "USED"
                else:
                    connection.execute("BEGIN IMMEDIATE")
                    cursor = connection.execute(
                        """UPDATE passes SET used=1, used_at=?, gate=?
                           WHERE code=? AND used=0 AND revoked=0""",
                        (now, gate, code),
                    )
                    if cursor.rowcount == 1:
                        row = connection.execute(
                            "SELECT serial FROM passes WHERE code=?", (code,)
                        ).fetchone()
                        result = {"status": "OK", "serial": row["serial"]}
                        log_result = "OK"
                    else:
                        row = connection.execute(
                            "SELECT serial,used,used_at,gate,revoked FROM passes WHERE code=?",
                            (code,),
                        ).fetchone()
                        if row is None or row["revoked"]:
                            result = {"status": "INVALID"}
                            log_result = "INVALID"
                        else:
                            result = {
                                "status": "USED",
                                "serial": row["serial"],
                                "used_at": row["used_at"],
                                "gate": row["gate"],
                            }
                            log_result = "USED"
                connection.execute(
                    "INSERT INTO scan_log(ts,code,result,gate) VALUES (?,?,?,?)",
                    (now, code, log_result, gate),
                )
        except Exception as exc:
            return json_db_error(exc)
        return jsonify(result)

    @app.get("/admin")
    def admin_page():
        return render_template_string(ADMIN_PAGE)

    @app.post("/admin/api/data")
    @admin_required
    def admin_data():
        try:
            with db() as connection:
                total = connection.execute("SELECT COUNT(*) FROM passes").fetchone()[0]
                entered = connection.execute(
                    "SELECT COUNT(*) FROM passes WHERE used=1 AND revoked=0"
                ).fetchone()[0]
                gates = connection.execute(
                    """SELECT gate,COUNT(*) AS count FROM passes
                       WHERE used=1 AND revoked=0 GROUP BY gate ORDER BY gate"""
                ).fetchall()
                scans = connection.execute(
                    """SELECT s.ts,s.code,s.result,s.gate,p.serial FROM scan_log s
                       LEFT JOIN passes p ON p.code=s.code ORDER BY s.id DESC LIMIT 50"""
                ).fetchall()
        except Exception as exc:
            return json_db_error(exc)
        return jsonify(
            total=int(total or 0),
            entered=int(entered or 0),
            remaining=int((total or 0) - (entered or 0)),
            gates=[dict(row) for row in gates],
            scans=[dict(row) for row in scans],
        )

    @app.post("/admin/api/search")
    @admin_required
    def admin_search():
        payload = request.get_json(silent=True) or {}
        query = str(payload.get("query") or "").strip()
        try:
            with db() as connection:
                if query.isdigit():
                    row = connection.execute(
                        "SELECT serial,code,used,used_at,gate,revoked FROM passes WHERE serial=?",
                        (int(query),),
                    ).fetchone()
                else:
                    row = connection.execute(
                        "SELECT serial,code,used,used_at,gate,revoked FROM passes WHERE code=?",
                        (query.upper(),),
                    ).fetchone()
        except Exception as exc:
            return json_db_error(exc)
        return jsonify({"pass": dict(row) if row else None})

    @app.post("/admin/api/action")
    @admin_required
    def admin_action():
        payload = request.get_json(silent=True) or {}
        action = payload.get("action")
        code = str(payload.get("code") or "").strip().upper()
        if action not in ("revoke", "undo") or not code:
            return jsonify(error="Expected action revoke/undo and a pass code"), 400
        try:
            with db() as connection:
                if app.config["USE_POSTGRES"]:
                    pass_row = connection.execute(
                        "SELECT serial,revoked FROM passes WHERE code=? FOR UPDATE",
                        (code,),
                    ).fetchone()
                else:
                    connection.execute("BEGIN IMMEDIATE")
                    pass_row = connection.execute(
                        "SELECT serial,revoked FROM passes WHERE code=?", (code,)
                    ).fetchone()
                if pass_row is None:
                    return jsonify(error="Pass not found"), 404
                if action == "revoke":
                    connection.execute("UPDATE passes SET revoked=1 WHERE code=?", (code,))
                    detail = "Pass revoked"
                else:
                    connection.execute(
                        "UPDATE passes SET used=0,used_at=NULL,gate=NULL WHERE code=?",
                        (code,),
                    )
                    detail = "Entry undone"
                connection.execute(
                    "INSERT INTO admin_audit(ts,action,code,detail) VALUES (?,?,?,?)",
                    (utc_now(), action.upper(), code, detail),
                )
        except Exception as exc:
            return json_db_error(exc)
        serial = pass_row["serial"]
        try:
            serial = int(serial)
        except Exception:
            pass
        return jsonify(status="OK", action=action, serial=serial)

    @app.get("/admin/export.csv")
    def admin_export():
        if not authorized_admin():
            return jsonify(error="Unauthorized"), 401
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(("serial", "code", "used", "used_at", "gate"))
        try:
            with db() as connection:
                rows = connection.execute(
                    "SELECT serial,code,used,used_at,gate FROM passes ORDER BY serial"
                )
                writer.writerows(tuple(row) for row in rows)
        except Exception as exc:
            return json_db_error(exc)
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=eventpass_checkins.csv"},
        )

    return app


app = create_app()


if __name__ == "__main__":
    if not str(app.config["STAFF_PIN"]).strip() or not str(app.config["ADMIN_PIN"]).strip():
        raise SystemExit("Set STAFF_PIN and ADMIN_PIN before starting EventPass.")
    init_db(app.config["DB_PATH"], use_postgres=app.config.get("USE_POSTGRES", False), database_url=app.config.get("DATABASE_URL"))
    port = int(os.environ.get("PORT", "5000"))
    try:
        from waitress import serve

        serve(app, host="0.0.0.0", port=port, threads=16)
    except ImportError:
        app.run(host="0.0.0.0", port=port, threaded=True)
