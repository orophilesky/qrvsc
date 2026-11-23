#!/usr/bin/env python3
"""Paced load test: check in unique issued passes at a target request rate."""

import argparse
import csv
import json
import statistics
import time
import urllib.error
import urllib.request
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:5000")
    parser.add_argument("--pin", required=True)
    parser.add_argument("--rate", type=float, default=20)
    parser.add_argument("--count", type=int, default=8000)
    args = parser.parse_args()
    with (Path(__file__).resolve().parent / "codes.csv").open(
        newline="", encoding="utf-8"
    ) as source:
        records = list(csv.DictReader(source))[: args.count]
    if len(records) != args.count:
        raise SystemExit(f"Requested {args.count} scans, found {len(records)} issued codes.")
    latencies = []
    interval = 1 / args.rate
    started = time.perf_counter()
    next_request = started
    for index, record in enumerate(records, start=1):
        delay = next_request - time.perf_counter()
        if delay > 0:
            time.sleep(delay)
        payload = json.dumps(
            {"code": record["code"], "gate": "Load Test", "pin": args.pin}
        ).encode()
        request = urllib.request.Request(
            args.url.rstrip("/") + "/api/scan",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        before = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                result = json.loads(response.read())
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise SystemExit(
                f"Scan {index} returned HTTP {error.code}: {detail}"
            ) from error
        elapsed = (time.perf_counter() - before) * 1000
        if result.get("status") != "OK":
            raise SystemExit(f"Scan {index} failed: {result}")
        latencies.append(elapsed)
        next_request = before + interval
        if index % 1000 == 0 or index == len(records):
            print(f"{index}/{len(records)} scans; latest latency {elapsed:.1f} ms")
    duration = time.perf_counter() - started
    ordered = sorted(latencies)
    p95 = ordered[max(0, int(len(ordered) * 0.95) - 1)]
    print(
        f"Completed {len(latencies)} unique scans in {duration:.1f}s "
        f"({len(latencies) / duration:.2f} req/s); "
        f"p50={statistics.median(latencies):.1f} ms, p95={p95:.1f} ms, "
        f"max={max(latencies):.1f} ms"
    )
    if len(latencies) == 8000 and p95 >= 200:
        raise SystemExit("FAIL: required p95 latency is below 200 ms.")


if __name__ == "__main__":
    main()
