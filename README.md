# EventPass

Lightweight, single-use gate check-in for hand-distributed, pre-printed passes.

## Assumptions

- One event and one shared database used by all gate devices.
- Every pass contains only a randomly generated code; no attendee details are stored.
- Scanner and admin credentials are supplied securely via environment variables and must not be committed. Admin access is protected at every data/action endpoint.
- An explicitly revoked pass is treated as invalid by the gate scanner. Admin actions and scan attempts are recorded for auditing.
- HTTPS is required for phone browser camera access. The scanner UI loads its QR library from a public CDN.

## Setup

Use Python 3.10 or newer:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## Generate the passes once

From the project directory, run the pass generation script to create the issue
database and print assets:

```powershell
python generate_qr.py
```

You can optionally provide a custom pass count. The generator refuses to
overwrite an existing database unless you explicitly force regeneration; only do
that before issuing/printing codes or when intentionally retiring the old issue.

Provide the print vendor with the generated QR image assets, print sheets, and
pass/code export produced by the script. Verify physical print size with the
vendor, and maintain a secure backup of the live database as the source of pass
state.

## Start the scanner

Set the required PINs and runtime settings in your environment (do not commit
real values), then start the threaded Waitress server:

```powershell
$env:STAFF_PIN = "<staff-pin>"
$env:ADMIN_PIN = "<admin-pin>"
$env:PORT = "<port>"
$env:DB_PATH = "<path-to-passes-db>"
python app.py
```

If Waitress is unavailable, the app has a threaded Flask development-server
fallback; use a production-ready threaded server for the event. Staff open the
scanner URL on their phone, enter the gate name and staff PIN, and allow camera
access. Admins open the admin page and authenticate with the admin PIN. The
admin PIN is kept in page memory and submitted with each admin request.

Run behind HTTPS (for example via a reverse proxy or a trusted tunnel/provider)
so phone cameras can access the scanner. The service applies per-IP scan
throttling and uses atomic single-use updates with a busy timeout for
concurrent check-ins.

## Tests and load test

Run the test suite:

```powershell
python -m pytest -q
```

The tests cover code uniqueness, concurrent single-use scanning, invalid and
unauthorized scans, lowercase normalization, generator overwrite safety, admin
audit, and QR decoding for a sample of generated PNGs.

With the scanner running and a fresh issue database, you can run a load test:

```powershell
python load_test.py --pin <staff-pin> --rate <requests-per-second> --count <number-of-codes>
```

The script reports latency percentiles and fails if the configured p95 threshold
is not met.

## Event-day checklist

- Rotate credentials from any test values; keep admin access restricted.
- Test-scan printed samples, including the smallest QR size you plan to use.
- Make a secure backup of the live database before doors open.
- Arrange backup internet/connectivity.
- Charge spare devices and power banks.
- Perform one test scan at each gate before doors open.
- Brief staff on normal scan outcomes and manual-entry procedures.
- After test scans only, reset check-in state using a trusted procedure
  appropriate for your deployment, and do not reset after actual doors-open
  check-ins.

Admin actions and scan attempts are retained in audit/log records.
