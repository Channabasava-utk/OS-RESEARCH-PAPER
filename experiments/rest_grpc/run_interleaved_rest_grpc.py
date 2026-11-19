#!/usr/bin/env python3
"""Randomized, Interleaved REST vs gRPC Matched Experiment Runner

Features:
- Same Ubuntu execution environment
- Both REST and gRPC servers kept alive and warm simultaneously
- Paired trial design with matched random seeds
- Balanced REST-first and gRPC-first ordering across trials (zero systematic position bias)
- Explicit separation of calibration (reps 1-3) and held-out validation (reps 4-10)
- Records per-trial metrics (p50, p95, p99, throughput, CPU, RSS, error rate)
- Preserves raw request-level latency samples
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
import random
import sys
import time
from dataclasses import asdict
from pathlib import Path

from ahisf.protocols import RunningAdapter
from ahisf.benchmark import Workload, benchmark_on_adapter, DEFAULT_WORKLOADS

WORKLOADS = {w.name: w for w in DEFAULT_WORKLOADS}
PROTOCOLS = ["rest_json", "grpc_protobuf"]


def write_csv(path: Path, rows: list[dict]):
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def run_interleaved_experiment(args):
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    repetitions = args.repetitions
    calib_reps = args.calib_reps
    duration = args.duration
    seed_base = args.seed

    print(f"\n=== Starting Randomized Interleaved REST/gRPC Evaluation ===")
    print(f"Platform: {platform.platform()} | Python: {sys.version}")
    print(f"Repetitions per workload: {repetitions} ({calib_reps} calibration + {repetitions - calib_reps} held-out validation)")
    print(f"Duration per trial: {duration} s | Workloads: {list(WORKLOADS.keys())}")

    trials_data = []
    raw_latencies = []
    order_records = []
    trial_counter = 1
    pair_counter = 1

    rng = random.Random(seed_base)

    # Launch both servers concurrently and maintain them warm
    with RunningAdapter("rest_json") as rest, RunningAdapter("grpc_protobuf") as grpc:
        adapters = {"rest_json": rest, "grpc_protobuf": grpc}

        # Warm up persistent channels
        print("Warming server channels...")
        for name, ad in adapters.items():
            client = ad.client_factory()
            for i in range(20):
                client.call(i, b"warmup" * 8)
            client.close()
        print("Warmup complete.")

        for rep in range(1, repetitions + 1):
            is_calib = 1 if rep <= calib_reps else 0
            phase_type = "calibration" if is_calib else "validation"

            for wl_name, wl_def in WORKLOADS.items():
                wl = Workload(**{**asdict(wl_def), "duration_s": duration})
                matched_seed = seed_base + rep * 10000 + pair_counter

                # Balance ordering: alternate or randomly shuffle with equal probability
                proto_order = PROTOCOLS.copy()
                if rep % 2 == 0:
                    proto_order.reverse()
                else:
                    if rng.random() > 0.5:
                        proto_order.reverse()

                order_records.append({
                    "pair_id": pair_counter,
                    "repetition": rep,
                    "workload": wl_name,
                    "first_protocol": proto_order[0],
                    "second_protocol": proto_order[1],
                    "phase": phase_type
                })

                print(f"Pair {pair_counter:02d} (rep {rep}/{repetitions}, {wl_name:13s}, {phase_type:11s}): order = {proto_order[0]} -> {proto_order[1]}")

                for order_pos, proto in enumerate(proto_order, 1):
                    res = benchmark_on_adapter(proto, wl, matched_seed, adapters[proto])

                    trial_row = {
                        "trial_id": trial_counter,
                        "pair_id": pair_counter,
                        "repetition": rep,
                        "is_calibration": is_calib,
                        "phase": phase_type,
                        "workload": wl_name,
                        "protocol": proto,
                        "order_in_pair": order_pos,
                        "payload_bytes": wl.payload_bytes,
                        "offered_rps": wl.offered_rps,
                        "duration_s": duration,
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
                        "encoded_network_mb_s": res["encoded_network_mb_s"],
                        "matched_seed": matched_seed
                    }
                    trials_data.append(trial_row)

                    for sample_idx, lat in enumerate(res["latency_samples_ms"], 1):
                        raw_latencies.append({
                            "trial_id": trial_counter,
                            "pair_id": pair_counter,
                            "repetition": rep,
                            "is_calibration": is_calib,
                            "workload": wl_name,
                            "protocol": proto,
                            "sample_index": sample_idx,
                            "scheduled_latency_ms": lat,
                            "matched_seed": matched_seed
                        })

                    print(f"  [{proto:13s} (pos {order_pos})] p99={res['p99_ms']:6.2f}ms tput={res['throughput_rps']:5.1f}rps cpu={res['cpu_pct_host']:4.1f}% completed={res['completed']}")
                    trial_counter += 1

                pair_counter += 1

    # Save outputs
    print(f"\nSaving results to {out}...")
    write_csv(out / "raw_trials.csv", trials_data)
    write_csv(out / "raw_request_latencies.csv", raw_latencies)
    write_csv(out / "interleaved_trial_order.csv", order_records)

    metadata = {
        "experiment": "Randomized Interleaved REST vs gRPC Matched Evaluation",
        "platform": platform.platform(),
        "python": sys.version,
        "repetitions": repetitions,
        "calibration_repetitions": calib_reps,
        "held_out_repetitions": repetitions - calib_reps,
        "trial_duration_s": duration,
        "workloads": list(WORKLOADS.keys()),
        "protocols": PROTOCOLS,
        "seed": seed_base,
        "total_trials": len(trials_data),
        "total_completed_requests": sum(r["completed"] for r in trials_data),
        "total_errors": sum(r["errors"] for r in trials_data),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    (out / "experiment_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(f"Finished successfully. Total trials: {len(trials_data)}, Requests completed: {metadata['total_completed_requests']}, Errors: {metadata['total_errors']}")
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="results/interleaved_rest_grpc")
    parser.add_argument("--repetitions", type=int, default=8)
    parser.add_argument("--calib-reps", type=int, default=3)
    parser.add_argument("--duration", type=float, default=1.5)
    parser.add_argument("--seed", type=int, default=20261116)
    args = parser.parse_args()
    run_interleaved_experiment(args)
