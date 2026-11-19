#!/usr/bin/env python3
"""Investigate repetition-level performance shifts, order effects, and variance

Recalculates empirical percentiles directly from raw request latencies (243,000 observations)
and performs formal non-parametric statistical hypothesis testing.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats


def run_shift_analysis(data_dir: Path, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    figures_dir = out_dir / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)

    raw_req_file = data_dir / "raw_request_latencies.csv"
    window_file = data_dir / "policy_window_results.csv"
    order_file = data_dir / "randomized_policy_order.csv"

    print(f"Loading raw request observations from {raw_req_file}...")
    df_raw = pd.read_csv(raw_req_file)
    print(f"Loaded {len(df_raw)} raw request records.")

    print(f"Loading window results from {window_file}...")
    df_win = pd.read_csv(window_file)

    print(f"Loading order records from {order_file}...")
    df_order = pd.read_csv(order_file)

    # 1. Recalculate percentiles directly from raw observations
    print("Recalculating p50, p95, p99 per (repetition, policy, window_index)...")
    raw_grp = df_raw.groupby(["repetition", "policy", "window_index", "workload"])["scheduled_latency_ms"].agg(
        raw_count="count",
        recomputed_p50_ms=lambda s: np.percentile(s, 50),
        recomputed_p95_ms=lambda s: np.percentile(s, 95),
        recomputed_p99_ms=lambda s: np.percentile(s, 99),
        raw_mean_ms="mean",
        raw_std_ms="std",
        raw_min_ms="min",
        raw_max_ms="max"
    ).reset_index()

    # Merge recomputed percentiles with reported window results
    merged = pd.merge(
        df_win,
        raw_grp,
        on=["repetition", "policy", "window_index", "workload"],
        how="inner"
    )
    print(f"Merged dataset has {len(merged)} windows.")

    # Check for discrepancy between reported and recomputed p99
    merged["p99_discrepancy_ms"] = merged["recomputed_p99_ms"] - merged["p99_ms"]
    max_disc = merged["p99_discrepancy_ms"].abs().max()
    print(f"Maximum absolute p99 discrepancy between reported and recomputed: {max_disc:.6f} ms")

    # 2. Repetition-Level Summary CSV
    # Aggregate across windows within each (repetition, policy, workload)
    rep_summary = merged.groupby(["repetition", "policy", "workload"]).agg(
        windows_count=("window_index", "count"),
        requests_sum=("completed", "sum"),
        errors_sum=("errors", "sum"),
        median_p50_ms=("recomputed_p50_ms", "median"),
        median_p95_ms=("recomputed_p95_ms", "median"),
        median_p99_ms=("recomputed_p99_ms", "median"),
        mean_p99_ms=("recomputed_p99_ms", "mean"),
        std_p99_ms=("recomputed_p99_ms", "std"),
        median_throughput_rps=("throughput_rps", "median"),
        mean_throughput_rps=("throughput_rps", "mean"),
        median_cpu_pct=("cpu_pct_host", "median"),
        mean_cpu_pct=("cpu_pct_host", "mean"),
        median_rss_mb=("peak_rss_mb", "median"),
        mean_rss_mb=("peak_rss_mb", "mean"),
    ).reset_index()

    rep_summary_file = out_dir / "repetition_level_summary.csv"
    rep_summary.to_csv(rep_summary_file, index=False)
    print(f"Saved repetition-level summary to {rep_summary_file}")

    # 3. Overall Repetition Performance Shift CSV
    # Comparing each repetition 1..10 across policies
    rep_shift = merged.groupby(["repetition", "policy"]).agg(
        p50_ms=("recomputed_p50_ms", "median"),
        p95_ms=("recomputed_p95_ms", "median"),
        p99_ms=("recomputed_p99_ms", "median"),
        mean_p99_ms=("recomputed_p99_ms", "mean"),
        std_p99_ms=("recomputed_p99_ms", "std"),
        throughput_rps=("throughput_rps", "mean"),
        cpu_pct_host=("cpu_pct_host", "mean"),
        peak_rss_mb=("peak_rss_mb", "max"),
        total_completed=("completed", "sum"),
        total_errors=("errors", "sum")
    ).reset_index()

    rep_shift_file = out_dir / "performance_shift_analysis.csv"
    rep_shift.to_csv(rep_shift_file, index=False)
    print(f"Saved performance shift analysis to {rep_shift_file}")

    # 4. Statistical Tests for Drift and Order Bias
    stat_findings = {}

    # Test A: Kruskal-Wallis H-test across repetitions for p99 by policy
    stat_findings["kruskal_wallis_p99_by_repetition"] = {}
    for pol in ["fixed_rest", "fixed_grpc", "adaptive_ahisf"]:
        sub = merged[merged["policy"] == pol]
        groups = [sub[sub["repetition"] == r]["recomputed_p99_ms"].values for r in range(1, 11)]
        h_stat, p_val = stats.kruskal(*groups)
        stat_findings["kruskal_wallis_p99_by_repetition"][pol] = {
            "h_statistic": float(h_stat),
            "p_value": float(p_val),
            "significant_alpha_0_05": bool(p_val < 0.05)
        }

    # Test B: Spearman correlation between repetition index (1..10) and p99, cpu, rss (Monotonic drift test)
    stat_findings["spearman_monotonic_drift_with_repetition"] = {}
    for pol in ["fixed_rest", "fixed_grpc", "adaptive_ahisf"]:
        sub = merged[merged["policy"] == pol]
        r_p99, p_p99 = stats.spearmanr(sub["repetition"], sub["recomputed_p99_ms"])
        r_cpu, p_cpu = stats.spearmanr(sub["repetition"], sub["cpu_pct_host"])
        r_rss, p_rss = stats.spearmanr(sub["repetition"], sub["peak_rss_mb"])
        stat_findings["spearman_monotonic_drift_with_repetition"][pol] = {
            "p99_latency": {"spearman_rho": float(r_p99), "p_value": float(p_p99)},
            "cpu_utilization": {"spearman_rho": float(r_cpu), "p_value": float(p_cpu)},
            "peak_rss_memory": {"spearman_rho": float(r_rss), "p_value": float(p_rss)},
        }

    # Test C: Policy Order Effect Analysis (Position 1 vs Position 2 vs Position 3)
    # merged has 'policy_order_index' (1, 2, 3)
    order_summary = merged.groupby(["policy", "policy_order_index"]).agg(
        count=("window_index", "count"),
        median_p99_ms=("recomputed_p99_ms", "median"),
        mean_p99_ms=("recomputed_p99_ms", "mean"),
        std_p99_ms=("recomputed_p99_ms", "std"),
        mean_cpu_pct=("cpu_pct_host", "mean"),
        mean_throughput_rps=("throughput_rps", "mean"),
    ).reset_index()

    order_csv = out_dir / "policy_order_effect_analysis.csv"
    order_summary.to_csv(order_csv, index=False)
    print(f"Saved policy order effect analysis to {order_csv}")

    stat_findings["kruskal_wallis_order_effect"] = {}
    for pol in ["fixed_rest", "fixed_grpc", "adaptive_ahisf"]:
        sub = merged[merged["policy"] == pol]
        g1 = sub[sub["policy_order_index"] == 1]["recomputed_p99_ms"].values
        g2 = sub[sub["policy_order_index"] == 2]["recomputed_p99_ms"].values
        g3 = sub[sub["policy_order_index"] == 3]["recomputed_p99_ms"].values
        h_stat, p_val = stats.kruskal(g1, g2, g3)
        stat_findings["kruskal_wallis_order_effect"][pol] = {
            "h_statistic": float(h_stat),
            "p_value": float(p_val),
            "significant_alpha_0_05": bool(p_val < 0.05)
        }

    # Save all test results to JSON
    json_path = out_dir / "statistical_shift_tests.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(stat_findings, f, indent=2)
    print(f"Saved statistical test results to {json_path}")

    # 5. Publication-Quality Charts
    # Chart 1: p99 Latency Across Repetitions (1 to 10) by Workload and Policy
    fig, axes = plt.subplots(1, 3, figsize=(16, 5), sharey=False)
    workloads = ["small_steady", "medium_bursty", "large_steady"]
    titles = ["Small Steady (64 B)", "Medium Bursty (4 KiB)", "Large Steady (64 KiB)"]
    pol_colors = {"fixed_rest": "#1f77b4", "fixed_grpc": "#ff7f0e", "adaptive_ahisf": "#2ca02c"}
    pol_labels = {"fixed_rest": "Fixed REST", "fixed_grpc": "Fixed gRPC", "adaptive_ahisf": "Adaptive AHISF"}

    for ax, wl, title in zip(axes, workloads, titles):
        for pol in ["fixed_rest", "fixed_grpc", "adaptive_ahisf"]:
            sub = rep_summary[(rep_summary["workload"] == wl) & (rep_summary["policy"] == pol)]
            ax.plot(
                sub["repetition"],
                sub["median_p99_ms"],
                marker="o",
                label=pol_labels[pol],
                color=pol_colors[pol],
                linewidth=1.8,
                markersize=6
            )
        ax.set_title(title, fontsize=12, fontweight="bold")
        ax.set_xlabel("Repetition Index (1–10)", fontsize=10)
        ax.set_ylabel("Median p99 Latency (ms)", fontsize=10)
        ax.set_xticks(range(1, 11))
        ax.grid(True, linestyle="--", alpha=0.5)
        ax.legend(fontsize=9)

    fig.suptitle("AHISF Three-Policy Repetition-Level p99 Trajectory Across 10 Repetitions", fontsize=14, fontweight="bold", y=1.02)
    fig.tight_layout()
    fig1_path = figures_dir / "repetition_p99_drift.png"
    fig.savefig(fig1_path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved figure {fig1_path}")

    # Chart 2: CPU Utilization and Peak RSS Memory Trajectory Across Repetitions
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))
    for pol in ["fixed_rest", "fixed_grpc", "adaptive_ahisf"]:
        sub = rep_shift[rep_shift["policy"] == pol]
        ax1.plot(sub["repetition"], sub["cpu_pct_host"], marker="s", label=pol_labels[pol], color=pol_colors[pol], linewidth=1.8)
        ax2.plot(sub["repetition"], sub["peak_rss_mb"], marker="^", label=pol_labels[pol], color=pol_colors[pol], linewidth=1.8)

    ax1.set_title("Mean Host CPU Utilization by Repetition", fontsize=11, fontweight="bold")
    ax1.set_xlabel("Repetition Index", fontsize=10)
    ax1.set_ylabel("Host CPU Utilization (%)", fontsize=10)
    ax1.set_xticks(range(1, 11))
    ax1.grid(True, linestyle="--", alpha=0.5)
    ax1.legend()

    ax2.set_title("Peak RSS Memory Footprint by Repetition", fontsize=11, fontweight="bold")
    ax2.set_xlabel("Repetition Index", fontsize=10)
    ax2.set_ylabel("Peak RSS (MB)", fontsize=10)
    ax2.set_xticks(range(1, 11))
    ax2.grid(True, linestyle="--", alpha=0.5)
    ax2.legend()

    fig.suptitle("System Resource Usage Stability Across 10 Repetitions", fontsize=13, fontweight="bold", y=1.02)
    fig.tight_layout()
    fig2_path = figures_dir / "repetition_cpu_rss_drift.png"
    fig.savefig(fig2_path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved figure {fig2_path}")

    # Chart 3: Policy Execution Order Position Effect (Position 1 vs 2 vs 3)
    fig, ax = plt.subplots(figsize=(8, 5))
    x_pos = np.arange(3)
    width = 0.25
    for i, pol in enumerate(["fixed_rest", "fixed_grpc", "adaptive_ahisf"]):
        sub = order_summary[order_summary["policy"] == pol].sort_values("policy_order_index")
        ax.bar(x_pos + (i - 1) * width, sub["mean_p99_ms"], width, label=pol_labels[pol], color=pol_colors[pol])

    ax.set_xticks(x_pos)
    ax.set_xticklabels(["Order 1 (First in Rep)", "Order 2 (Second in Rep)", "Order 3 (Third in Rep)"], fontsize=10)
    ax.set_ylabel("Mean p99 Latency (ms)", fontsize=10)
    ax.set_title("Impact of Intra-Repetition Policy Execution Order on p99 Latency", fontsize=12, fontweight="bold")
    ax.grid(axis="y", linestyle="--", alpha=0.5)
    ax.legend()
    fig.tight_layout()
    fig3_path = figures_dir / "policy_order_position_effect.png"
    fig.savefig(fig3_path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved figure {fig3_path}")

    print("Phase 3 shift analysis completed successfully.")
    return stat_findings


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="historical_data/historical_three_policy")
    parser.add_argument("--out-dir", default="results/repetition_shift")
    args = parser.parse_args()
    run_shift_analysis(Path(args.data_dir), Path(args.out_dir))
