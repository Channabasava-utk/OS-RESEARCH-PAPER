"""Re-analyse the AHISF benchmark output and regenerate publication figures."""
from __future__ import annotations
import json
import random
import statistics
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results_full"
FIGURES = ROOT / "figures"


def bootstrap_median_difference(a, b, seed=919, iterations=10000):
    """Bootstrap independent trial medians; return gRPC - REST difference and CI."""
    rng = random.Random(seed)
    a = list(a)
    b = list(b)
    observed = statistics.median(b) - statistics.median(a)
    diffs = []
    for _ in range(iterations):
        aa = [a[rng.randrange(len(a))] for _ in a]
        bb = [b[rng.randrange(len(b))] for _ in b]
        diffs.append(statistics.median(bb) - statistics.median(aa))
    diffs.sort()
    return {
        "grpc_minus_rest_median_difference": observed,
        "ci95_low": diffs[int(0.025 * iterations)],
        "ci95_high": diffs[int(0.975 * iterations)],
        "interpretation": "CI excludes zero" if diffs[int(0.025 * iterations)] > 0 or diffs[int(0.975 * iterations)] < 0 else "CI includes zero",
    }


def main():
    FIGURES.mkdir(exist_ok=True)
    trials = pd.read_csv(RESULTS / "raw_trials.csv")
    metadata = json.loads((RESULTS / "experiment_results.json").read_text(encoding="utf-8"))
    validation_start = metadata["method"]["calibration_repetitions_per_candidate"] + 1
    holdout = trials[trials["repetition"] >= validation_start]

    effects = {}
    for workload in holdout["workload"].unique():
        data = holdout[holdout["workload"] == workload]
        rest = data[data["protocol"] == "rest_json"]
        grpc = data[data["protocol"] == "grpc_protobuf"]
        effects[workload] = {}
        for metric in ["p99_ms", "throughput_rps", "cpu_pct_host", "encoded_network_mb_s"]:
            effects[workload][metric] = bootstrap_median_difference(
                rest[metric].tolist(), grpc[metric].tolist(), seed=919 + len(workload) + len(metric)
            )

    # Add validation effect sizes to the machine-readable result file.
    metadata["validation_effects_grpc_minus_rest"] = effects
    (RESULTS / "experiment_results.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    summary = pd.read_csv(RESULTS / "summary.csv")
    workloads = ["small_steady", "medium_bursty", "large_steady"]
    labels = ["Small / steady\n64 B", "Medium / bursty\n4 KiB", "Large / steady\n64 KiB"]
    protocols = ["rest_json", "grpc_protobuf"]
    nice = {"rest_json": "REST / JSON", "grpc_protobuf": "gRPC / Protobuf"}

    # Figure 1: median per-trial p99 with bootstrap 95% CI.
    fig, ax = plt.subplots(figsize=(8.5, 4.8))
    x = list(range(len(workloads)))
    width = 0.34
    for j, protocol in enumerate(protocols):
        vals, lower, upper = [], [], []
        for workload in workloads:
            row = summary[(summary.workload == workload) & (summary.candidate == protocol)].iloc[0]
            v = float(row.median_trial_p99_ms)
            lo = float(row.median_trial_p99_ci95_low_ms)
            hi = float(row.median_trial_p99_ci95_high_ms)
            vals.append(v)
            lower.append(v - lo)
            upper.append(hi - v)
        ax.bar([a + (j - 0.5) * width for a in x], vals, width, label=nice[protocol],
               yerr=[lower, upper], capsize=4)
    ax.set_xticks(x, labels)
    ax.set_ylabel("Median trial p99 latency (ms)")
    ax.set_title("Tail latency by workload (30 held-out trials per candidate)")
    ax.legend()
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(FIGURES / "p99_latency.png", dpi=220)
    plt.close(fig)

    # Figure 2: throughput by workload.
    fig, ax = plt.subplots(figsize=(8.5, 4.8))
    for j, protocol in enumerate(protocols):
        vals = [float(summary[(summary.workload == workload) & (summary.candidate == protocol)].iloc[0].throughput_rps)
                for workload in workloads]
        ax.bar([a + (j - 0.5) * width for a in x], vals, width, label=nice[protocol])
    ax.set_xticks(x, labels)
    ax.set_ylabel("Achieved throughput (requests/s)")
    ax.set_title("Achieved throughput under open-loop offered load")
    ax.legend()
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(FIGURES / "throughput.png", dpi=220)
    plt.close(fig)

    # Figure 3: CPU use by workload.
    fig, ax = plt.subplots(figsize=(8.5, 4.8))
    for j, protocol in enumerate(protocols):
        vals = [float(summary[(summary.workload == workload) & (summary.candidate == protocol)].iloc[0].cpu_pct_host)
                for workload in workloads]
        ax.bar([a + (j - 0.5) * width for a in x], vals, width, label=nice[protocol])
    ax.set_xticks(x, labels)
    ax.set_ylabel("Process CPU (% of host affinity capacity)")
    ax.set_title("CPU utilisation by workload")
    ax.legend()
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(FIGURES / "cpu_utilisation.png", dpi=220)
    plt.close(fig)

    # Compact machine-readable report for the standalone write-up.
    output = {
        "validation_repetitions": int(summary.trial_count.iloc[0]),
        "effects": effects,
        "ahisf_decisions": {d["workload"]: d["selected"] for d in metadata["decisions"]},
        "median_controller_decision_time_ms": metadata["median_controller_decision_time_ms"],
        "total_completed_requests": int(holdout.completed.sum()),
        "total_errors": int(holdout.errors.sum()),
    }
    (RESULTS / "analysis.json").write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
