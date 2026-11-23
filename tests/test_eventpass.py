import csv
import random
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import pytest

from app import create_app, init_db
from generate_qr import generate_codes


@pytest.fixture
def event_app(tmp_path):
    path = tmp_path / "passes.db"
    init_db(path)
    with sqlite3.connect(path) as connection:
        connection.executemany(
            "INSERT INTO passes(serial,code) VALUES (?,?)",
            [(1, "ABCDEFGHJKLM"), (2, "23456789ABCD")],
        )
    application = create_app(
        {"TESTING": True, "DB_PATH": str(path), "STAFF_PIN": "1234", "ADMIN_PIN": "9876"}
    )
    return application, path


def scan(app, code, pin="1234", gate="Gate A"):
    with app.test_client() as client:
        return client.post(
            "/api/scan", json={"code": code, "gate": gate, "pin": pin}
        )


def test_generates_8000_unique_codes():
    codes = generate_codes(8000)
    assert len(codes) == 8000
    assert all(len(code) == 12 and set(code) <= set("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for code in codes)


def test_atomic_single_use_under_concurrency(event_app):
    app, path = event_app
    barrier = threading.Barrier(32)

    def concurrent_scan():
        barrier.wait()
        response = scan(app, "abcdefghjklm")
        return response.status_code, response.get_json()["status"]

    with ThreadPoolExecutor(max_workers=32) as pool:
        results = list(pool.map(lambda _: concurrent_scan(), range(32)))
    assert sum(status == "OK" for _, status in results) == 1
    assert sum(status == "USED" for _, status in results) == 31
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM scan_log").fetchone()[0] == 32


def test_invalid_wrong_pin_and_lowercase(event_app):
    app, _ = event_app
    assert scan(app, "not-a-pass").get_json()["status"] == "INVALID"
    assert scan(app, "ABCDEFGHJKLM", pin="wrong").status_code == 401
    response = scan(app, "abcdefghjklm")
    assert response.get_json() == {"serial": 1, "status": "OK"}


def test_admin_audit_export_and_protection(event_app):
    app, path = event_app
    with app.test_client() as client:
        assert client.post("/admin/api/data", json={"pin": "bad"}).status_code == 401
        assert client.post(
            "/admin/api/action",
            json={"pin": "9876", "action": "revoke", "code": "ABCDEFGHJKLM"},
        ).status_code == 200
        assert client.post(
            "/admin/api/action",
            json={"pin": "9876", "action": "undo", "code": "ABCDEFGHJKLM"},
        ).status_code == 200
        assert client.get(
            "/admin/export.csv", headers={"X-Admin-Pin": "9876"}
        ).status_code == 200
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM admin_audit").fetchone()[0] == 2


def test_generated_png_decodes_random_sample():
    root = Path(__file__).resolve().parents[1]
    csv_path = root / "codes.csv"
    if not csv_path.exists():
        pytest.skip("Run generate_qr.py first to create the requested QR bundle.")
    with csv_path.open(newline="", encoding="utf-8") as source:
        records = list(csv.DictReader(source))
    sample = random.SystemRandom().sample(records, min(50, len(records)))
    detector = cv2.QRCodeDetector()
    for record in sample:
        image = cv2.imread(str(root / "qr_codes" / record["file"]))
        decoded, points, _ = detector.detectAndDecode(image)
        if points is None or decoded != record["code"]:
            image = cv2.resize(image, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
            decoded, points, _ = detector.detectAndDecode(image)
        assert points is not None
        assert decoded == record["code"]


def test_generator_refuses_existing_database(tmp_path, monkeypatch, capsys):
    import generate_qr

    existing_db = tmp_path / "passes.db"
    existing_db.write_text("keep this database", encoding="utf-8")
    monkeypatch.setattr(generate_qr, "ROOT", tmp_path)
    monkeypatch.setattr(generate_qr, "DB_PATH", existing_db)
    assert generate_qr.main(["2"]) == 2
    assert "Refusing to overwrite" in capsys.readouterr().err
    assert existing_db.read_text(encoding="utf-8") == "keep this database"
