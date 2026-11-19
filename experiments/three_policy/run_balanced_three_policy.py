#!/usr/bin/env python3
"""Matched-Environment Balanced Three-Policy Experiment Runner

Features:
- Balanced Latin-Square permutation of policy execution order (zero positional bias)
- Same Ubuntu execution environment for calibration and evaluation
- Matched pseudo-random seeds per (repetition, window) across policies
- Both REST and gRPC server processes kept alive and warm
- Full system telemetry: Host/client/server CPU, RSS, Linux PSI pressure, and cgroup throttling
- Preserves raw request observations separate from aggregated window metrics
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
import sys
import time
from dataclasses import asdict
from pathlib import Path

from ahisf.protocols import RunningAdapter
from ahisf.benchmark import Workload, benchmark_on_adapter, DEFAULT_WORKLOADS
from ahisf.controller import AHISFConfig, AHISFController, InteractionContract, MetricProfile

WORKLOADS = {w.name: w for w in DEFAULT_WORKLOADS}
POLICIES = ["fixed_rest", "fixed_grpc", "adaptive_ahisf"]

DEFAULT_SCHEDULE = [
    "small_steady", "small_steady",
    "medium_bursty", "medium_bursty",
    "large_steady", "large_steady",
    "medium_bursty", "medium_bursty",
    "small_steady", "small_steady",
]

# 6 Balanced permutations: Each policy appears in position 1, 2, and 3 exactly twice
BALANCED_PERMUTATIONS = [
    ["fixed_rest", "fixed_grpc", "adaptive_ahisf"],
    ["fixed_rest", "adaptive_ahisf", "fixed_grpc"],
    ["fixed_grpc", "fixed_rest", "adaptive_ahisf"],
    ["fixed_grpc", "adaptive_ahisf", "fixed_rest"],
    ["adaptive_ahisf", "fixed_rest", "fixed_grpc"],
    ["adaptive_ahisf", "fixed_grpc", "fixed_rest"],
]


def read_psi_pressure() -> dict[str, float]:
    """Read Linux Pressure Stall Information (PSI) if supported."""
    res = {}
    for res_type in ["cpu", "memory", "io"]:
        p = Path(f"/proc/pressure/{res_type}")
        if p.exists():
            try:
                for line in p.read_text().splitlines():
                    parts = line.strip().split()
                    if len(parts) >= 5:
                        kind = parts[0]  # some or full
                        total_val = float(parts[4].split("=")[1])
                        res[f"psi_{res_type}_{kind}_total_us"] = total_val
            except Exception:
                pass
    return res


def read_cgroup_throttling() -> dict[str, int]:
    """Read cgroup v2 CPU throttling statistics if available."""
    res = {"cgroup_throttled_periods": 0, "cgroup_throttled_usec": 0}
    p = Path("/sys/fs/cgroup/cpu.stat")
    if p.exists():
        try:
            for line in p.read_text().splitlines():
                if line.startswith("nr_throttled"):
                    res["cgroup_throttled_periods"] = int(line.split()[1])
                elif line.startswith("throttled_usec"):
                    res["cgroup_throttled_usec"] = int(line.split()[1])
        except Exception:
            pass
    return res


def load_calibration_profiles(csv_path: Path, max_calib_rep: int = 3) -> dict[str, dict[str, MetricProfile]]:
    with csv_path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    result = {}
    for wl in ["small_steady", "medium_bursty", "large_steady"]:
        result[wl] = {}
        for proto in ("rest_json", "grpc_protobuf"):
            subset = [r for r in rows if r["workload"] == wl and r["protocol"] == proto and int(r["repetition"]) <= max_calib_rep]
            if not subset:
                raise ValueError(f"No calibration rows found for {wl}/{proto} in {csv_path}")
            med = lambda key: float(sorted([float(r[key]) for r in subset])[len(subset) // 2])
            result[wl][proto] = MetricProfile(
                p99_ms=med("p99_ms"),
                throughput_rps=med("throughput_rps"),
                cpu_pct=med("cpu_pct_host"),
                memory_mb=med("peak_rss_mb"),
                encoded_network_mb_s=med("encoded_network_mb_s"),
                error_rate=med("error_rate"),
                sample_age_s=0.0,
            )
    return result


def write_csv(path: Path, rows: list[dict]):
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def run_balanced_experiment(args):
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    calib_path = Path(args.calibration).resolve()
    if not calib_path.exists():
        raise FileNotFoundError(f"Calibration file missing: {calib_path}")

    print(f"Loading local matched calibration profiles from {calib_path}...")
    calibration = load_calibration_profiles(calib_path, max_calib_rep=args.calib_reps)

    windows, latencies, decisions, order_rows, telemetry_rows = [], [], [], [], []
    repetitions = args.repetitions
    duration = args.duration
    seed_base = args.seed

    print(f"\n=== Starting Matched Balanced 3-Policy Evaluation ===")
    print(f"Platform: {platform.platform()} | Python: {sys.version}")
    print(f"Repetitions: {repetitions} | Window duration: {duration} s | Schedule length: {len(DEFAULT_SCHEDULE)}")
    print(f"Latin-Square Policy Order Balancing across {repetitions} repetitions.")

    for rep in range(1, repetitions + 1):
        # Pick permutation cyclically to maintain strict position balance
        perm_idx = (rep - 1) % len(BALANCED_PERMUTATIONS)
        policy_order = BALANCED_PERMUTATIONS[perm_idx].copy()

        print(f"\n--- Repetition {rep}/{repetitions} | Policy Order: {' -> '.join(policy_order)} ---")

        for order_idx, policy in enumerate(policy_order, 1):
            order_rows.append({"repetition": rep, "policy_order_index": order_idx, "policy": policy})
            controller = AHISFController(
                InteractionContract(max_p99_ms=args.max_p99_ms, max_error_rate=args.max_error_rate),
                AHISFConfig(
                    max_p99_ms=args.max_p99_ms,
                    max_error_rate=args.max_error_rate,
                    cpu_budget_pct=90.0,
                    memory_budget_mb=512.0,
                    network_budget_mb_s=100.0,
                    hysteresis_delta=0.05,
                    consecutive_windows=2,
                    min_residence_windows=1,
                    max_metric_age_s=2.0
                ),
                initial_route="rest_json"
            )

            with RunningAdapter("rest_json") as rest, RunningAdapter("grpc_protobuf") as grpc:
                adapters = {"rest_json": rest, "grpc_protobuf": grpc}

                for win_idx, wl_name in enumerate(DEFAULT_SCHEDULE, 1):
                    wl = Workload(**{**asdict(WORKLOADS[wl_name]), "duration_s": duration})

                    # Read system pressure telemetry before window
                    psi_before = read_psi_pressure()
                    cgroup_before = read_cgroup_throttling()

                    if policy == "fixed_rest":
                        selected, status, changed, score_json = "rest_json", "fixed_policy", False, "[]"
                    elif policy == "fixed_grpc":
                        selected, status, changed, score_json = "grpc_protobuf", "fixed_policy", False, "[]"
                    else:
                        decision = controller.decide(
                            calibration[wl_name],
                            demand_rps=wl.offered_rps,
                            window_id=f"{rep}-{win_idx}"
                        )
                        selected = decision.get("selected") or controller.current_route
                        status = decision.get("status")
                        changed = bool(decision.get("changed"))
                        score_json = json.dumps(decision.get("scores", []), sort_keys=True)
                        decisions.append({
                            "repetition": rep,
                            "window_index": win_idx,
                            "workload": wl_name,
                            "selected_protocol": selected,
                            "changed": changed,
                            "status": status,
                            "scores": score_json,
                            "selection_basis": "frozen_calibration_profile_for_known_workload_phase"
                        })

                    # Paired random seed per (repetition, window)
                    trial_seed = seed_base + rep * 10000 + win_idx
                    res = benchmark_on_adapter(selected, wl, trial_seed, adapters[selected])

                    # Read system pressure telemetry after window
                    psi_after = read_psi_pressure()
                    cgroup_after = read_cgroup_throttling()

                    row = {k: v for k, v in res.items() if not k.endswith("_samples_ms")}
                    row.update({
                        "repetition": rep,
                        "policy": policy,
                        "policy_order_index": order_idx,
                        "window_index": win_idx,
                        "workload": wl_name,
                        "selected_protocol": selected,
                        "controller_status": status,
                        "route_changed": changed,
                        "trial_seed": trial_seed
                    })
                    windows.append(row)

                    for sample_idx, lat_val in enumerate(res["latency_samples_ms"], 1):
                        latencies.append({
                            "repetition": rep,
                            "policy": policy,
                            "window_index": win_idx,
                            "workload": wl_name,
                            "selected_protocol": selected,
                            "sample_index": sample_idx,
                            "scheduled_latency_ms": lat_val,
                            "trial_seed": trial_seed
                        })

                    telem_entry = {
                        "repetition": rep,
                        "policy": policy,
                        "window_index": win_idx,
                        "workload": wl_name,
                        "cgroup_throttled_periods_delta": cgroup_after["cgroup_throttled_periods"] - cgroup_before["cgroup_throttled_periods"],
                        "cgroup_throttled_usec_delta": cgroup_after["cgroup_throttled_usec"] - cgroup_before["cgroup_throttled_usec"],
                    }
                    telem_entry.update(psi_after)
                    telemetry_rows.append(telem_entry)

                    print(f"rep={rep}/{repetitions} pol={policy[:10]:10s} win={win_idx:02d}/10 wl={wl_name:13s} "
                          f"route={selected:13s} p99={row['p99_ms']:6.2f}ms completed={row['completed']} err={row['errors']}")

    # Save outputs
    print(f"\nWriting results to {out}...")
    write_csv(out / "policy_window_results.csv", windows)
    write_csv(out / "raw_request_latencies.csv", latencies)
    write_csv(out / "adaptive_decisions.csv", decisions)
    write_csv(out / "balanced_policy_order.csv", order_rows)
    write_csv(out / "system_pressure_telemetry.csv", telemetry_rows)

    metadata = {
        "design": "matched balanced three-policy changing-workload experiment",
        "policies": POLICIES,
        "repetitions": repetitions,
        "window_duration_s": duration,
        "schedule": DEFAULT_SCHEDULE,
        "calibration_file": str(calib_path),
        "calibration_rule": "median of fresh local calibration trials",
        "sha256_calibration_csv": hashlib.sha256(calib_path.read_bytes()).hexdigest(),
        "order_balancing": "Latin-square cyclic permutations across repetitions",
        "host": platform.platform(),
        "python": sys.version,
        "seed": seed_base,
        "windows_completed": len(windows),
        "requests_completed": sum(int(r["completed"]) for r in windows),
        "errors": sum(int(r["errors"]) for r in windows),
    }
    (out / "experiment_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(f"Execution complete! Total windows: {len(windows)}, Total completed requests: {metadata['requests_completed']}, Errors: {metadata['errors']}")
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="results/matched_three_policy")
    parser.add_argument("--calibration", default="calibration/local_matched_calibration.csv")
    parser.add_argument("--calib-reps", type=int, default=3)
    parser.add_argument("--repetitions", type=int, default=6)
    parser.add_argument("--duration", type=float, default=1.5)
    parser.add_argument("--seed", type=int, default=20261116)
    parser.add_argument("--max-p99-ms", type=float, default=1500.0)
    parser.add_argument("--max-error-rate", type=float, default=0.05)
    args = parser.parse_args()
    run_balanced_experiment(args)
