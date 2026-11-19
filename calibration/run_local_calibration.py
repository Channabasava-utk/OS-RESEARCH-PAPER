#!/usr/bin/env python3
"""Run calibration trials in the current local environment to produce fresh matched calibration profiles."""
from __future__ import annotations

import argparse
import csv
import json
import platform
import sys
import time
from dataclasses import asdict
from pathlib import Path

from ahisf.protocols import RunningAdapter
from ahisf.benchmark import Workload, benchmark_on_adapter, DEFAULT_WORKLOADS

WORKLOADS = {w.name: w for w in DEFAULT_WORKLOADS}
PROTOCOLS = ["rest_json", "grpc_protobuf"]


def run_calibration(repetitions: int = 3, duration_s: float = 1.5, seed_base: int = 42, out_csv: Path = None):
    if out_csv is None:
        out_csv = Path(__file__).resolve().parent / "local_matched_calibration.csv"
    out_csv.parent.mkdir(parents=True, exist_ok=True)

    rows = []
    print(f"=== Starting Local Calibration on {platform.platform()} ===")
    print(f"Python: {sys.version}")
    print(f"Repetitions: {repetitions}, Duration per trial: {duration_s} s")

    trial_id = 1
    for rep in range(1, repetitions + 1):
        for wl_name, wl_def in WORKLOADS.items():
            wl = Workload(**{**asdict(wl_def), "duration_s": duration_s})
            for proto in PROTOCOLS:
                seed = seed_base + rep * 1000 + trial_id
                print(f"Calib trial {trial_id}: rep={rep}/{repetitions} proto={proto} workload={wl_name} ...", end=" ", flush=True)
                with RunningAdapter(proto) as adapter:
                    res = benchmark_on_adapter(proto, wl, seed, adapter)
                
                print(f"p99={res['p99_ms']:.2f} ms, tput={res['throughput_rps']:.1f} rps, cpu={res['cpu_pct_host']:.1f}%")
                row = {
                    "trial_id": trial_id,
                    "repetition": rep,
                    "is_calibration": 1,
                    "workload": wl_name,
                    "protocol": proto,
                    "payload_bytes": wl.payload_bytes,
                    "offered_rps": wl.offered_rps,
                    "duration_s": duration_s,
                    "concurrency": wl.concurrency,
                    "requested": res["requested"],
                    "completed": res["completed"],
                    "errors": res["errors"],
                    "error_rate": res["error_rate"],
                    "throughput_rps": res["throughput_rps"],
                    "p50_ms": res["p50_ms"],
                    "p95_ms": res["p95_ms"],
                    "p99_ms": res["p99_ms"],
                    "cpu_pct_host": res["cpu_pct_host"],
                    "client_cpu_pct_host": res["client_cpu_pct_host"],
                    "server_cpu_pct_host": res["server_cpu_pct_host"],
                    "peak_rss_mb": res["peak_rss_mb"],
                    "encoded_network_mb_s": res["encoded_network_mb_s"]
                }
                rows.append(row)
                trial_id += 1

    with out_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nCalibration complete! Saved {len(rows)} trials to {out_csv}")
    return out_csv


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--duration", type=float, default=1.5)
    parser.add_argument("--out", type=str, default=None)
    args = parser.parse_args()
    out_p = Path(args.out).resolve() if args.out else None
    run_calibration(args.repetitions, args.duration, out_csv=out_p)
