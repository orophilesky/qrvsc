#!/usr/bin/env python3
"""Generate a one-time EventPass issue and print bundle."""

import argparse
import csv
import secrets
import shutil
import sqlite3
import sys
from pathlib import Path

import qrcode
from qrcode.constants import ERROR_CORRECT_Q
from qrcode.image.svg import SvgPathImage
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas


ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "passes.db"
OUTPUT_DIRS = ("qr_codes", "qr_svg", "print_sheets")


def generate_codes(count):
    if count < 1:
        raise ValueError("count must be a positive integer")
    codes = set()
    while len(codes) < count:
        codes.add("".join(secrets.choice(ALPHABET) for _ in range(12)))
    return codes


def create_database(path):
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=10000")
        connection.executescript(
            """
            CREATE TABLE passes (
                serial INTEGER PRIMARY KEY,
                code TEXT UNIQUE NOT NULL,
                used INTEGER DEFAULT 0,
                used_at TEXT,
                gate TEXT,
                revoked INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE scan_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                code TEXT NOT NULL,
                result TEXT NOT NULL,
                gate TEXT
            );
            CREATE TABLE admin_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                action TEXT NOT NULL,
                code TEXT NOT NULL,
                detail TEXT
            );
            CREATE INDEX scan_log_ts_idx ON scan_log(ts);
            CREATE INDEX scan_log_gate_idx ON scan_log(gate);
            """
        )
        return connection
    except Exception:
        connection.close()
        raise


def make_qr(code, image_factory=None, box_size=20):
    qr = qrcode.QRCode(
        version=4,
        error_correction=ERROR_CORRECT_Q,
        box_size=box_size,
        border=4,
    )
    qr.add_data(code)
    qr.make(fit=True)
    if image_factory is None:
        return qr.make_image(fill_color="black", back_color="white")
    return qr.make_image(image_factory=image_factory)


def draw_crop_marks(pdf, x, y, width, height):
    pdf.setLineWidth(0.25)
    mark = 2.5 * mm
    gap = 1 * mm
    for xx in (x, x + width):
        for yy in (y, y + height):
            horizontal_start = xx - mark if xx == x else xx + gap
            horizontal_end = xx - gap if xx == x else xx + mark
            vertical_start = yy - mark if yy == y else yy + gap
            vertical_end = yy - gap if yy == y else yy + mark
            pdf.line(horizontal_start, yy, horizontal_end, yy)
            pdf.line(xx, vertical_start, xx, vertical_end)


def draw_vector_qr(pdf, code, x, y, size):
    qr = qrcode.QRCode(version=4, error_correction=ERROR_CORRECT_Q, border=4)
    qr.add_data(code)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    module = size / len(matrix)
    pdf.setFillColorRGB(0, 0, 0)
    for row, values in enumerate(matrix):
        for column, dark in enumerate(values):
            if dark:
                pdf.rect(
                    x + column * module,
                    y + (len(matrix) - row - 1) * module,
                    module,
                    module,
                    stroke=0,
                    fill=1,
                )


def create_print_sheets(records, output_dir):
    path = output_dir / "eventpass_print_sheets.pdf"
    pdf = canvas.Canvas(str(path), pagesize=A4, pageCompression=1)
    page_width, page_height = A4
    margin = 8 * mm
    columns, rows = 4, 6
    cell_width = (page_width - 2 * margin) / columns
    cell_height = (page_height - 2 * margin) / rows
    qr_size = 30 * mm
    for index, (serial, code, _filename) in enumerate(records):
        slot = index % (columns * rows)
        if index and slot == 0:
            pdf.showPage()
        column, row = slot % columns, slot // columns
        cell_x = margin + column * cell_width
        cell_top = page_height - margin - row * cell_height
        qr_x = cell_x + (cell_width - qr_size) / 2
        qr_y = cell_top - 37 * mm
        draw_vector_qr(pdf, code, qr_x, qr_y, qr_size)
        pdf.setFont("Helvetica-Bold", 9)
        pdf.drawCentredString(cell_x + cell_width / 2, qr_y - 4 * mm, f"Serial {serial:04d}")
        draw_crop_marks(pdf, qr_x, qr_y, qr_size, qr_size)
    pdf.save()
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description="Generate unique EventPass QR codes.")
    parser.add_argument("count", nargs="?", type=int, default=8000)
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace the existing database and generated output directories",
    )
    args = parser.parse_args(argv)
    if args.count < 1:
        parser.error("count must be a positive integer")
    if DB_PATH.exists() and not args.force:
        print(
            f"Refusing to overwrite {DB_PATH}. Use --force only if you intend to "
            "replace the issued passes.",
            file=sys.stderr,
        )
        return 2
    if args.force:
        if DB_PATH.exists():
            DB_PATH.unlink()
        for directory in OUTPUT_DIRS:
            target = ROOT / directory
            if target.exists():
                shutil.rmtree(target)
        csv_path = ROOT / "codes.csv"
        if csv_path.exists():
            csv_path.unlink()

    codes = sorted(generate_codes(args.count))
    for directory in OUTPUT_DIRS:
        (ROOT / directory).mkdir(parents=True, exist_ok=True)

    connection = create_database(DB_PATH)
    records = []
    try:
        for serial, code in enumerate(codes, start=1):
            filename = f"{serial:04d}_{code}.png"
            png_path = ROOT / "qr_codes" / filename
            svg_path = ROOT / "qr_svg" / f"{serial:04d}_{code}.svg"
            make_qr(code).save(png_path)
            make_qr(code, image_factory=SvgPathImage, box_size=1).save(svg_path)
            connection.execute(
                "INSERT INTO passes(serial, code) VALUES (?, ?)", (serial, code)
            )
            records.append((serial, code, filename))
            if serial % 100 == 0 or serial == args.count:
                print(f"\rGenerating QR assets: {serial}/{args.count}", end="", flush=True)
        connection.commit()
        print()
        with (ROOT / "codes.csv").open("w", newline="", encoding="utf-8") as csv_file:
            writer = csv.writer(csv_file)
            writer.writerow(("serial", "code", "file"))
            writer.writerows(records)
        pdf_path = create_print_sheets(records, ROOT / "print_sheets")
    except Exception:
        connection.close()
        raise
    connection.close()
    print(f"Created {args.count} passes in {DB_PATH}")
    print(f"Vendor files: qr_codes/, qr_svg/, print_sheets/ ({pdf_path.name}), codes.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
