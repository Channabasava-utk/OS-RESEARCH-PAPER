"""Matched, repeated three-policy AHISF experiment.

Policies: fixed REST, fixed gRPC, and profile-based adaptive AHISF.
The adaptive policy receives a predeclared workload-phase label and consults
frozen calibration profiles from primary repetitions 1-5. This is an
experiment controller, not a production traffic router or online learner.
"""
from __future__ import annotations
import argparse, csv, json, random, statistics, hashlib, platform, sys
from pathlib import Path
from dataclasses import asdict

from ahisf.protocols import RunningAdapter
from ahisf.benchmark import Workload, benchmark_on_adapter
from ahisf.controller import AHISFConfig, AHISFController, InteractionContract, MetricProfile

WORKLOADS = {
    "small_steady": Workload("small_steady", 64, 180, 3.0, 32, "64-byte steady; 180 offered req/s"),
    "medium_bursty": Workload("medium_bursty", 4096, 420, 3.0, 48, "4-KiB periodic burst; 420 offered req/s"),
    "large_steady": Workload("large_steady", 65536, 150, 3.0, 48, "64-KiB steady; 150 offered req/s"),
}
# Two windows per phase allow the configured two-consecutive-window hysteresis
# rule to respond. Repeated phases also test return transitions.
DEFAULT_SCHEDULE = [
    "small_steady", "small_steady",
    "medium_bursty", "medium_bursty",
    "large_steady", "large_steady",
    "medium_bursty", "medium_bursty",
    "small_steady", "small_steady",
]
POLICIES = ["fixed_rest", "fixed_grpc", "adaptive_ahisf"]


def load_calibration(csv_path: Path) -> dict[str, dict[str, MetricProfile]]:
    with csv_path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    result = {}
    for workload in WORKLOADS:
        result[workload] = {}
        for protocol in ("rest_json", "grpc_protobuf"):
            subset = [r for r in rows if r["workload"] == workload and r["protocol"] == protocol
                      and int(r["repetition"]) <= 5]
            if len(subset) != 5:
                raise ValueError(f"Expected exactly 5 calibration trials for {workload}/{protocol}; got {len(subset)}")
            med = lambda key: statistics.median(float(r[key]) for r in subset)
            result[workload][protocol] = MetricProfile(
                p99_ms=med("p99_ms"), throughput_rps=med("throughput_rps"),
                cpu_pct=med("cpu_pct_host"), memory_mb=med("peak_rss_mb"),
                encoded_network_mb_s=med("encoded_network_mb_s"), error_rate=med("error_rate"),
                sample_age_s=0.0,
            )
    return result


def write_csv(path: Path, rows: list[dict]):
    if not rows:
        raise ValueError(f"No rows to write: {path}")
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader(); writer.writerows(rows)


def run(args):
    root = Path(__file__).resolve().parent
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    if args.calibration:
        calibration_path = Path(args.calibration).resolve()
    else:
        # Search relative to experiment script and project root
        candidate_paths = [
            root.parent.parent / "calibration" / "primary_raw_trials.csv",
            root.parent.parent / "historical_data" / "primary_process_separated" / "raw_trials.csv",
            root / "primary_process_separated_results" / "raw_trials.csv",
            Path("primary_process_separated_results/raw_trials.csv").resolve(),
            Path("raw_trials_1.csv").resolve()
        ]
        calibration_path = None
        for p in candidate_paths:
            if p.exists():
                calibration_path = p
                break
        if calibration_path is None:
            raise FileNotFoundError(f"Could not find primary calibration raw_trials.csv in candidates: {candidate_paths}")
    calibration = load_calibration(calibration_path)
    schedule = DEFAULT_SCHEDULE
    windows, latencies, decisions, order_rows = [], [], [], []
    seed_base = args.seed

    for rep in range(1, args.repetitions + 1):
        policy_order = POLICIES.copy()
        random.Random(seed_base + rep).shuffle(policy_order)
        for policy_order_index, policy in enumerate(policy_order, 1):
            order_rows.append({"repetition": rep, "policy_order_index": policy_order_index, "policy": policy})
            controller = AHISFController(
                InteractionContract(max_p99_ms=args.max_p99_ms, max_error_rate=args.max_error_rate),
                AHISFConfig(max_p99_ms=args.max_p99_ms, max_error_rate=args.max_error_rate,
                            cpu_budget_pct=90, memory_budget_mb=512, network_budget_mb_s=100,
                            hysteresis_delta=0.05, consecutive_windows=2,
                            min_residence_windows=1, max_metric_age_s=2.0),
                initial_route="rest_json",
            )
            # Start both independent server processes for each policy schedule;
            # keep them alive and warm through all workload phases in that schedule.
            with RunningAdapter("rest_json") as rest, RunningAdapter("grpc_protobuf") as grpc:
                adapters = {"rest_json": rest, "grpc_protobuf": grpc}
                for window_index, workload_name in enumerate(schedule, 1):
                    workload = Workload(**{**asdict(WORKLOADS[workload_name]), "duration_s": args.duration})
                    if policy == "fixed_rest":
                        selected, status, changed, score_json = "rest_json", "fixed_policy", False, "[]"
                    elif policy == "fixed_grpc":
                        selected, status, changed, score_json = "grpc_protobuf", "fixed_policy", False, "[]"
                    else:
                        # This is explicit phase-aware profile selection. The phase
                        # label is supplied by the experiment schedule, not inferred
                        # from live telemetry. The calibration data are frozen.
                        decision = controller.decide(
                            calibration[workload_name], demand_rps=workload.offered_rps,
                            window_id=f"{rep}-{window_index}")
                        selected = decision.get("selected") or controller.current_route
                        status = decision.get("status")
                        changed = bool(decision.get("changed"))
                        score_json = json.dumps(decision.get("scores", []), sort_keys=True)
                        decisions.append({"repetition": rep, "window_index": window_index,
                                          "workload": workload_name, "selected_protocol": selected,
                                          "changed": changed, "status": status,
                                          "scores": score_json,
                                          "selection_basis": "frozen_calibration_profile_for_known_workload_phase"})
                    # Same seed for a given repetition/window across all policies.
                    # Thus request payload bytes and arrival schedule are paired.
                    trial_seed = seed_base + rep * 10000 + window_index
                    result = benchmark_on_adapter(selected, workload, trial_seed, adapters[selected])
                    row = {k: v for k, v in result.items() if not k.endswith("_samples_ms")}
                    row.update({"repetition": rep, "policy": policy, "policy_order_index": policy_order_index,
                                "window_index": window_index, "workload": workload_name,
                                "selected_protocol": selected, "controller_status": status,
                                "route_changed": changed, "trial_seed": trial_seed})
                    windows.append(row)
                    for sample_index, value in enumerate(result["latency_samples_ms"], 1):
                        latencies.append({"repetition": rep, "policy": policy, "window_index": window_index,
                                          "workload": workload_name, "selected_protocol": selected,
                                          "sample_index": sample_index, "scheduled_latency_ms": value,
                                          "trial_seed": trial_seed})
                    print(f"rep={rep}/{args.repetitions} policy={policy} window={window_index}/{len(schedule)} "
                          f"workload={workload_name} route={selected} p99={row['p99_ms']:.3f} "
                          f"completed={row['completed']} errors={row['errors']}", flush=True)

    write_csv(out / "policy_window_results.csv", windows)
    write_csv(out / "raw_request_latencies.csv", latencies)
    write_csv(out / "adaptive_decisions.csv", decisions or [{"note": "no adaptive decisions"}])
    write_csv(out / "randomized_policy_order.csv", order_rows)
    metadata = {
        "design": "matched repeated three-policy changing-workload experiment",
        "policies": POLICIES,
        "repetitions": args.repetitions,
        "window_duration_s": args.duration,
        "schedule": schedule,
        "workload_definitions": {k: asdict(v) for k, v in WORKLOADS.items()},
        "calibration_file": str(calibration_path),
        "calibration_rule": "median of primary dataset repetitions 1-5 for each adapter/workload; frozen before this run",
        "adaptive_policy": "AHISF controller with 5% hysteresis, two consecutive windows, one-window minimum residence; phase label known to experiment controller",
        "adaptive_limitation": "not a live telemetry-driven router; no online counterfactual learning; current phase label is supplied by deterministic schedule",
        "paired_seed_rule": "same repetition/window seed across all policies",
        "randomization": "policy order independently shuffled within each repetition using seed+repetition",
        "server_lifecycle": "both REST and gRPC server processes started before each policy schedule and kept warm through its phases",
        "switching_cost": "not included in timed window latency; report separately using measure_switch.py",
        "host": platform.platform(), "python": sys.version,
        "seed": seed_base,
        "windows": len(windows), "requests_completed": sum(int(r["completed"]) for r in windows),
        "errors": sum(int(r["errors"]) for r in windows),
        "sha256_calibration_csv": hashlib.sha256(calibration_path.read_bytes()).hexdigest(),
    }
    (out / "experiment_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print("\nCOMPLETED", json.dumps({k: metadata[k] for k in ("windows", "requests_completed", "errors", "repetitions")}, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="results_three_policy")
    parser.add_argument("--calibration", default=None)
    parser.add_argument("--repetitions", type=int, default=10)
    parser.add_argument("--duration", type=float, default=3.0)
    parser.add_argument("--seed", type=int, default=20261116)
    parser.add_argument("--max-p99-ms", type=float, default=100.0)
    parser.add_argument("--max-error-rate", type=float, default=0.01)
    parser.add_argument("--smoke", action="store_true", help="Allow fewer repetitions and shorter windows for smoke tests")
    args = parser.parse_args()
    if not args.smoke:
        if args.repetitions < 10:
            parser.error("Use at least 10 independent schedule repetitions for the planned confirmatory run.")
        if args.duration < 1.0:
            parser.error("Use windows of at least 1.0 s; default is 3.0 s.")
    run(args)

if __name__ == "__main__":
    main()
