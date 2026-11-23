# EventPass

Lightweight, single-use gate check-in for hand-distributed, pre-printed passes.

## Assumptions

- One event and one SQLite database shared by all gate phones.
- Every pass contains only a randomly generated 12-character code; no attendee details are stored.
- The scanner and admin PINs are secrets supplied via environment variables. Admin access is PIN-gated at every data/action endpoint.
- An explicitly revoked pass is treated as invalid by the gate scanner. The audit table records admin revocations and undo-entry actions.
- HTTPS is required for phone browser camera access. The HTML scanner library is pinned to html5-qrcode 2.3.8 and loaded from its public CDN.

## Setup

Use Python 3.10 or newer:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## Generate the passes once

From the project directory, run:

```powershell
python generate_qr.py
```

The default creates 8,000 unique codes. A custom count is available, e.g.
`python generate_qr.py 500`. The generator refuses to overwrite an existing
`passes.db`. `--force` deliberately replaces the generated database and output
folders; only use it before issuing/printing codes, or when intentionally
retiring the old issue.

Give the print vendor:

- `qr_codes/` — 820px+ black-on-white PNGs (version 4, 20px modules)
- `qr_svg/` — vector QR images
- `print_sheets/eventpass_print_sheets.pdf` — A4, 4-by-6 vector QR sheets with serials and crop marks
- `codes.csv` — serial, code, and corresponding PNG filename

The PDF uses 30mm QR symbols; verify physical print size with the vendor.
Maintain a secure backup of `passes.db`; it is the live source of pass state.

## Start the scanner

Set two different PINs, then start the threaded Waitress server:

```powershell
$env:STAFF_PIN = "2619"
$env:ADMIN_PIN = "5100"
$env:PORT = "5000"
$env:DB_PATH = ".\passes.db"
python app.py
```

If Waitress is unavailable, the app has a threaded Flask development-server
fallback; use Waitress (or a threaded Gunicorn deployment on Linux) for the
event. Staff open `https://your-event-host/` on their phone, enter the gate
name/PIN, and allow camera access. Admins open `/admin`. The admin page PIN is
kept in the page memory and submitted with each admin request.

For a quick HTTPS tunnel:

```powershell
cloudflared tunnel --url http://localhost:5000
```

Use the HTTPS URL printed by cloudflared. Alternatively deploy behind HTTPS on
Render or Railway. The scanner applies an in-memory per-IP limit of 30 requests
per second; the database uses WAL mode, a 10-second busy timeout, and atomic
single-use updates.

## Tests and load test

Run the test suite:

```powershell
python -m pytest -q
```

The tests cover 8,000-code uniqueness, concurrent single-use scanning, invalid
and unauthorized scans, lowercase normalization, generator overwrite safety,
admin audit, and QR decoding for a random sample of 50 generated PNGs.

With the scanner running and a fresh issue database, run the full 8,000-code
20-request/second load test (about 6m 40s):

```powershell
python load_test.py --pin $env:STAFF_PIN --rate 20 --count 8000
```

The script reports p50/p95/max latency and fails if the 8,000-scan p95 is
200ms or greater.

## Event-day checklist

- Change `STAFF_PIN` and `ADMIN_PIN` from any test values; keep the admin PIN restricted.
- Test-scan printed samples, including the smallest QR (minimum 2.5 x 2.5 cm).
- Make a secure backup copy of `passes.db` before doors open.
- Arrange a hotspot/second SIM as backup internet.
- Charge spare phones and a power bank.
- Perform one test scan at each gate before doors open.
- Brief staff on success, already-used, invalid, network-error, and manual-entry flows.
- After test scans only, reset check-in state from a trusted local SQLite shell:

  ```sql
  UPDATE passes SET used=0, used_at=NULL, gate=NULL;
  DELETE FROM scan_log;
  ```

  Also clear any test revocations if needed:

  ```sql
  UPDATE passes SET revoked=0;
  ```

Do not reset after actual doors-open check-ins. Admin undo/revoke actions are
recorded in `admin_audit`; scan attempts are retained in `scan_log`.
