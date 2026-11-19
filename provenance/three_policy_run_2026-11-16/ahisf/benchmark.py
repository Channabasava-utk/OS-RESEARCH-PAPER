"""Open-loop benchmark harness for the AHISF request/response prototype."""
from __future__ import annotations

import argparse
import concurrent.futures
import csv
import json
import os
import random
import statistics
import threading
import time
import platform
import importlib.metadata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import psutil

from .controller import (
    AHISFConfig, AHISFController, InteractionContract, MetricProfile,
)
from .protocols import RunningAdapter


@dataclass(frozen=True)
class Workload:
    name: str
    payload_bytes: int
    offered_rps: float
    duration_s: float
    concurrency: int
    description: str


DEFAULT_WORKLOADS = [
    Workload("small_steady", 64, 180, 3.0, 32,
             "64-byte request, 180 offered requests/s, steady arrival"),
    Workload("medium_bursty", 4096, 420, 3.0, 48,
             "4-KiB request, 420 offered requests/s, periodic burst arrivals"),
    Workload("large_steady", 65536, 150, 3.0, 48,
             "64-KiB request, 150 offered requests/s, steady arrival"),
]


class RSSSampler:
    """Sample peak RSS for the benchmark client and independent server process."""
    def __init__(self, processes: list[psutil.Process], interval_s: float = 0.005):
        self.processes = processes
        self.interval_s = interval_s
        self.maximum_by_pid: dict[int, int] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _run(self):
        while not self._stop.is_set():
            for process in self.processes:
                try:
                    rss = process.memory_info().rss
                    self.maximum_by_pid[process.pid] = max(self.maximum_by_pid.get(process.pid, 0), rss)
                except psutil.Error:
                    pass
            self._stop.wait(self.interval_s)

    def __enter__(self):
        for process in self.processes:
            try:
                self.maximum_by_pid[process.pid] = process.memory_info().rss
            except psutil.Error:
                self.maximum_by_pid[process.pid] = 0
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)


def affinity_cpu_count() -> int:
    if hasattr(os, "sched_getaffinity"):
        return max(1, len(os.sched_getaffinity(0)))
    return max(1, os.cpu_count() or 1)


def percentile(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    vals = sorted(values)
    pos = (len(vals) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(vals) - 1)
    frac = pos - lo
    return vals[lo] * (1 - frac) + vals[hi] * frac


def bootstrap_median_ci(values: list[float], seed: int = 73, iterations: int = 2000) -> tuple[float, float]:
    if not values:
        return float("nan"), float("nan")
    rng = random.Random(seed)
    n = len(values)
    medians = []
    for _ in range(iterations):
        sample = [values[rng.randrange(n)] for _ in range(n)]
        medians.append(statistics.median(sample))
    return percentile(medians, 0.025), percentile(medians, 0.975)


def _run_call(client, request_id: int, payload: bytes, planned_at: float):
    started = time.perf_counter()
    rid, size, digest, req_bytes, resp_bytes = client.call(request_id, payload)
    finished = time.perf_counter()
    expected = __import__("hashlib").sha256(payload).digest()
    if rid != request_id or size != len(payload) or digest != expected:
        raise RuntimeError("response validation failed")
    return {
        "scheduled_latency_ms": (finished - planned_at) * 1000.0,
        "service_roundtrip_ms": (finished - started) * 1000.0,
        "encoded_bytes": req_bytes + resp_bytes,
    }


def benchmark_on_adapter(protocol: str, workload: Workload, seed: int, adapter) -> dict[str, Any]:
    """Run one open-loop trial against an already-started server."""
    rng = random.Random(seed)
    payload = rng.randbytes(workload.payload_bytes)
    # Warm connections before timing; the server remains up across repetitions.
    warm = adapter.client_factory()
    for i in range(100):
        _run_call(warm, i, payload, time.perf_counter())
    warm.close()

    client_process = psutil.Process(os.getpid())
    server_process = psutil.Process(adapter.server_process.pid)
    client_cpu_before = time.process_time()
    server_cpu_before = sum(server_process.cpu_times()[:2])
    started_wall = time.perf_counter()
    with RSSSampler([client_process, server_process]) as sampler:
        latencies: list[float] = []
        service_latencies: list[float] = []
        encoded_bytes = 0
        errors = 0
        futures: list[concurrent.futures.Future] = []
        n_requests = int(workload.offered_rps * workload.duration_s)
        local = threading.local()
        clients_lock = threading.Lock()
        clients = []

        def task(req_id: int, planned: float):
            client = getattr(local, "client", None)
            if client is None:
                client = adapter.client_factory()
                local.client = client
                with clients_lock:
                    clients.append(client)
            return _run_call(client, req_id, payload, planned)

        with concurrent.futures.ThreadPoolExecutor(max_workers=workload.concurrency) as pool:
            for i in range(n_requests):
                planned = started_wall + i / workload.offered_rps
                # For burst workload, every fifth of the interval carries 2.5x arrivals.
                if workload.name == "medium_bursty":
                    # Each 100-request cycle puts 20% of arrivals into the first 5%
                    # of the cycle (4x average rate); the remaining arrivals are
                    # spread over the remaining 95% of the cycle.
                    cycle_num = i // 100
                    local_i = i % 100
                    cycle_start = started_wall + cycle_num * (100 / workload.offered_rps)
                    if local_i < 20:
                        planned = cycle_start + local_i * (0.25 / workload.offered_rps)
                    else:
                        planned = cycle_start + (5.0 / workload.offered_rps) + (
                            (local_i - 20) * (1.1875 / workload.offered_rps)
                        )
                delay = planned - time.perf_counter()
                if delay > 0:
                    time.sleep(delay)
                futures.append(pool.submit(task, i, planned))
            for future in concurrent.futures.as_completed(futures):
                try:
                    r = future.result()
                    latencies.append(r["scheduled_latency_ms"])
                    service_latencies.append(r["service_roundtrip_ms"])
                    encoded_bytes += r["encoded_bytes"]
                except Exception:
                    errors += 1
        for client in clients:
            try:
                client.close()
            except Exception:
                pass
        elapsed = time.perf_counter() - started_wall
        client_cpu_elapsed = time.process_time() - client_cpu_before
        try:
            server_cpu_elapsed = max(0.0, sum(server_process.cpu_times()[:2]) - server_cpu_before)
        except psutil.Error:
            server_cpu_elapsed = 0.0
        client_peak_rss = max(sampler.maximum_by_pid.get(client_process.pid, 0), client_process.memory_info().rss)
        server_peak_rss = sampler.maximum_by_pid.get(server_process.pid, 0)
        cpu_elapsed = client_cpu_elapsed + server_cpu_elapsed
        peak_rss = client_peak_rss + server_peak_rss

    successes = len(latencies)
    requested = n_requests
    return {
        "protocol": protocol,
        "workload": workload.name,
        "payload_bytes": workload.payload_bytes,
        "offered_rps": workload.offered_rps,
        "duration_s": workload.duration_s,
        "concurrency": workload.concurrency,
        "requested": requested,
        "completed": successes,
        "errors": errors,
        "error_rate": errors / max(1, requested),
        "throughput_rps": successes / max(elapsed, 1e-9),
        "p50_ms": percentile(latencies, 0.50),
        "p95_ms": percentile(latencies, 0.95),
        "p99_ms": percentile(latencies, 0.99),
        "service_p99_ms": percentile(service_latencies, 0.99),
        "cpu_pct_host": 100.0 * cpu_elapsed / max(elapsed, 1e-9) / affinity_cpu_count(),
        "client_cpu_pct_host": 100.0 * client_cpu_elapsed / max(elapsed, 1e-9) / affinity_cpu_count(),
        "server_cpu_pct_host": 100.0 * server_cpu_elapsed / max(elapsed, 1e-9) / affinity_cpu_count(),
        "client_cpu_seconds": client_cpu_elapsed,
        "server_cpu_seconds": server_cpu_elapsed,
        "peak_rss_mb": peak_rss / (1024 * 1024),
        "client_peak_rss_mb": client_peak_rss / (1024 * 1024),
        "server_peak_rss_mb": server_peak_rss / (1024 * 1024),
        "encoded_network_mb_s": encoded_bytes / max(elapsed, 1e-9) / (1024 * 1024),
        "validation": "all completed responses verified (request id, payload length, SHA-256)",
        "latency_samples_ms": latencies,
        "service_latency_samples_ms": service_latencies,
    }


def benchmark_once(protocol: str, workload: Workload, seed: int = 1) -> dict[str, Any]:
    """Convenience wrapper for a single isolated trial and server lifecycle."""
    with RunningAdapter(protocol) as adapter:
        return benchmark_on_adapter(protocol, workload, seed, adapter)


def summarise(rows: list[dict[str, Any]], protocol: str, workload: str,
              sample_rows: list[dict[str, Any]], rep_min: int | None = None,
              rep_max: int | None = None) -> dict[str, float]:
    subset = [r for r in rows if r["protocol"] == protocol and r["workload"] == workload
              and (rep_min is None or r["repetition"] >= rep_min)
              and (rep_max is None or r["repetition"] <= rep_max)]
    if not subset:
        raise ValueError(f"No results for {protocol}/{workload}")
    selected_reps = {r["repetition"] for r in subset}
    samples = [x["scheduled_latency_ms"] for x in sample_rows
               if x["protocol"] == protocol and x["workload"] == workload
               and x["repetition"] in selected_reps]
    service_samples = [x["service_roundtrip_ms"] for x in sample_rows
                       if x["protocol"] == protocol and x["workload"] == workload
                       and x["repetition"] in selected_reps]
    trial_p99 = [r["p99_ms"] for r in subset]
    lo, hi = bootstrap_median_ci(trial_p99, seed=91 + len(subset))
    req_total = sum(r["requested"] for r in subset)
    err_total = sum(r["errors"] for r in subset)
    return {
        "p50_ms": percentile(samples, 0.50),
        "p95_ms": percentile(samples, 0.95),
        "p99_ms": percentile(samples, 0.99),
        "service_p99_ms": percentile(service_samples, 0.99),
        "median_trial_p99_ms": statistics.median(trial_p99),
        "median_trial_p99_ci95_low_ms": lo,
        "median_trial_p99_ci95_high_ms": hi,
        "throughput_rps": statistics.median(r["throughput_rps"] for r in subset),
        "cpu_pct_host": statistics.median(r["cpu_pct_host"] for r in subset),
        "peak_rss_mb": statistics.median(r["peak_rss_mb"] for r in subset),
        "encoded_network_mb_s": statistics.median(r["encoded_network_mb_s"] for r in subset),
        "error_rate": err_total / max(1, req_total),
        "completed_requests": sum(r["completed"] for r in subset),
        "latency_sample_count": len(samples),
        "trial_count": len(subset),
    }


def median_profile(summary: dict[str, float]) -> MetricProfile:
    return MetricProfile(
        p99_ms=summary["p99_ms"],
        throughput_rps=summary["throughput_rps"],
        cpu_pct=summary["cpu_pct_host"],
        memory_mb=summary["peak_rss_mb"],
        encoded_network_mb_s=summary["encoded_network_mb_s"],
        error_rate=summary["error_rate"],
    )


def _cpu_model() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text(errors="ignore").splitlines():
            if line.lower().startswith(("model name", "hardware")):
                return line.split(":", 1)[-1].strip()
    except OSError:
        pass
    return "Not reported by platform interface"


def _cpu_governor() -> str:
    try:
        values = sorted(Path("/sys/devices/system/cpu").glob("cpu[0-9]*/cpufreq/scaling_governor"))
        found = sorted({p.read_text().strip() for p in values})
        return ", ".join(found) if found else "Not exposed"
    except OSError:
        return "Not exposed"


def _package_versions() -> dict[str, str]:
    packages = ["grpcio", "protobuf", "requests", "uvicorn", "starlette", "psutil"]
    versions = {}
    for package in packages:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "not-installed"
    return versions


def run_experiment(out_dir: Path, repetitions: int = 15, duration_s: float = 3.0,
                   workloads: list[Workload] | None = None, seed: int = 20261114) -> dict[str, Any]:
    workloads = workloads or [
        Workload(w.name, w.payload_bytes, w.offered_rps, duration_s, w.concurrency, w.description)
        for w in DEFAULT_WORKLOADS
    ]
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = out_dir / "raw_trials.csv"
    results: list[dict[str, Any]] = []
    request_samples: list[dict[str, Any]] = []
    protocols = ["rest_json", "grpc_protobuf"]

    if repetitions < 4:
        raise ValueError("Use at least four repetitions: calibration plus independent validation")
    calibration_repetitions = max(4, repetitions // 3)
    if validation_repetitions := repetitions - calibration_repetitions:
        pass
    if validation_repetitions < 6:
        raise ValueError("Use at least 10 repetitions to retain >=4 calibration and >=6 validation trials")

    # Randomise workload order and protocol block order. Each server is started once
    # per workload/protocol to avoid measuring server startup/shutdown as IPC latency.
    workload_order = list(enumerate(workloads))
    random.Random(seed).shuffle(workload_order)
    protocol_orders = {}
    for wi, workload in workload_order:
        order = protocols.copy()
        random.Random(seed + wi).shuffle(order)
        protocol_orders[workload.name] = order
        for protocol in order:
            with RunningAdapter(protocol) as adapter:
                for rep in range(repetitions):
                    row = benchmark_on_adapter(protocol, workload, seed + wi * 10000 + rep, adapter)
                    latency_samples = row.pop("latency_samples_ms")
                    service_samples = row.pop("service_latency_samples_ms")
                    row["repetition"] = rep + 1
                    row["workload_description"] = workload.description
                    results.append(row)
                    for scheduled_ms, service_ms in zip(latency_samples, service_samples):
                        request_samples.append({
                            "protocol": protocol, "workload": workload.name, "repetition": rep + 1,
                            "scheduled_latency_ms": scheduled_ms, "service_roundtrip_ms": service_ms,
                        })
                    print(
                        f"[{workload.name:14s}] {protocol:13s} rep={rep+1:02d}/{repetitions} "
                        f"p99={row['p99_ms']:.2f}ms thr={row['throughput_rps']:.1f}/s "
                        f"err={row['error_rate']:.3f}", flush=True
                    )

    with raw_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        writer.writeheader()
        writer.writerows(results)

    sample_path = out_dir / "raw_request_latencies.csv"
    with sample_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(request_samples[0].keys()))
        writer.writeheader()
        writer.writerows(request_samples)

    # Per-workload summaries are used as observed candidate profiles; these are measured data.
    # The controller runs after both compatible candidates have been tested for this workload.
    config = AHISFConfig(
        max_p99_ms=100.0,
        max_error_rate=0.01,
        cpu_budget_pct=90.0,
        memory_budget_mb=512.0,
        network_budget_mb_s=100.0,
        hysteresis_delta=0.05,
        consecutive_windows=2,
        min_residence_windows=2,
    )
    contract = InteractionContract(
        interaction="request_response", topology="network", authentication="none",
        encryption_required=False, required_delivery="at_most_once",
        max_p99_ms=100.0, max_error_rate=0.01,
        supported_candidates=("rest_json", "grpc_protobuf"),
    )
    controller = AHISFController(contract, config, initial_route="rest_json")
    decisions = []
    summaries: dict[str, dict[str, dict[str, float]]] = {}
    controller_timing_ms: list[float] = []
    for workload in workloads:
        workload_rows = [r for r in results if r["workload"] == workload.name]
        calibration_rows = [r for r in workload_rows if r["repetition"] <= calibration_repetitions]
        validation_rows = [r for r in workload_rows if r["repetition"] > calibration_repetitions]

        validation_summaries = {
            protocol: summarise(validation_rows, protocol, workload.name, request_samples) for protocol in protocols
        }
        calibration_summaries = {
            protocol: summarise(calibration_rows, protocol, workload.name, request_samples) for protocol in protocols
        }
        summaries[workload.name] = validation_summaries

        # Use independent calibration blocks to create decision windows.  The final
        # validation repetitions are never used to choose the candidate.
        window_decisions = []
        block_size = max(1, calibration_repetitions // 5)
        for wi_block, first_rep in enumerate(range(1, calibration_repetitions + 1, block_size), start=1):
            last_rep = min(calibration_repetitions, first_rep + block_size - 1)
            block_rows = [r for r in calibration_rows if first_rep <= r["repetition"] <= last_rep]
            block_summaries = {
                protocol: summarise(block_rows, protocol, workload.name, request_samples) for protocol in protocols
            }
            profiles = {p: median_profile(block_summaries[p]) for p in protocols}
            t0 = time.perf_counter()
            decision = controller.decide(profiles, workload.offered_rps,
                                          window_id=f"{workload.name}:{first_rep}-{last_rep}")
            controller_timing_ms.append((time.perf_counter() - t0) * 1000.0)
            window_decisions.append({
                "window_id": f"{first_rep}-{last_rep}",
                "calibration_profiles": block_summaries,
                "decision": decision,
            })

        decisions.append({
            "workload": workload.name,
            "demand_rps": workload.offered_rps,
            "calibration_repetitions_per_candidate": calibration_repetitions,
            "validation_repetitions_per_candidate": validation_repetitions,
            "calibration_summaries": calibration_summaries,
            "validation_summaries": validation_summaries,
            "decision_windows": window_decisions,
            "selected": controller.current_route,
            "selected_candidate_validation": validation_summaries[controller.current_route],
        })

    # In this static replay experiment AHISF's selected route uses the same independent trial
    # distribution as its corresponding fixed candidate. Report that equivalence explicitly.
    report = {
        "title": "AHISF prototype experiment: REST/JSON vs gRPC/Protobuf",
        "date": time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "environment": {
            "platform": platform.platform(),
            "kernel": platform.release(),
            "processor": platform.processor() or _cpu_model(),
            "python": os.sys.version,
            "logical_cpu_count": os.cpu_count(),
            "affinity_cpu_count": len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else os.cpu_count(),
            "memory_total_gb": round(psutil.virtual_memory().total / (1024**3), 2),
            "cpu_governor": _cpu_governor(),
            "package_versions": _package_versions(),
            "topology": "Client and server run as distinct operating-system processes on the same Linux host, communicating over IPv4 loopback (127.0.0.1).",
            "security": "Plaintext HTTP/1.1 and gRPC over HTTP/2; TLS/mTLS disabled.",
        },
        "method": {
            "total_repetitions_per_candidate_per_workload": repetitions,
            "calibration_repetitions_per_candidate": calibration_repetitions,
            "independent_validation_repetitions_per_candidate": validation_repetitions,
            "trial_duration_s": duration_s,
            "arrival_model": "open-loop scheduled arrivals; medium workload uses periodic burst remapping",
            "execution_order": {k: v for k, v in protocol_orders.items()},
            "warmup_requests_per_trial": 100,
            "client_workers": {w.name: w.concurrency for w in workloads},
            "grpc_server_workers": 16,
            "rest_server": "Uvicorn/Starlette in separate server process; keep-alive enabled",
            "connection_reuse": "HTTP connection pooling via requests.Session; one reusable gRPC channel per client worker thread",
            "validation": "Every completed response checked for request id, payload length and SHA-256",
            "tail_latency_aggregation": f"Request-level latency samples pooled across {validation_repetitions} independent validation trials; median trial p99 and bootstrap 95% CI also reported",
            "latency_definition": "p50/p95/p99 are scheduled-arrival-to-response completion latency; service_p99 excludes client-side executor queue time",
            "resource_note": "CPU sums benchmark-client and server-process CPU time, divided by elapsed time and CPU affinity count. RSS is the sum of separately sampled client/server peak RSS. Network bytes are encoded application request+response bytes, not packet-captured wire bytes.",
        },
        "weights": asdict(config.weights),
        "controller_config": {
            "hysteresis_delta": config.hysteresis_delta,
            "consecutive_windows": config.consecutive_windows,
            "max_p99_ms": config.max_p99_ms,
            "cpu_budget_pct": config.cpu_budget_pct,
            "memory_budget_mb": config.memory_budget_mb,
            "network_budget_mb_s": config.network_budget_mb_s,
        },
        "workloads": [asdict(w) for w in workloads],
        "decisions": decisions,
        "median_controller_decision_time_ms": statistics.median(controller_timing_ms) if controller_timing_ms else 0.0,
        "raw_trials_csv": raw_path.name,
        "raw_request_latencies_csv": sample_path.name,
        "interpretation_limitations": [
            "This prototype evaluates only semantically compatible synchronous request-response adapters.",
            "REST uses HTTP/1.1 JSON with Base64 payloads; gRPC uses HTTP/2 and dynamically described Protocol Buffer messages.",
            "No broker or shared-memory adapter is substituted for request-response because those mechanisms have different contracts or topology constraints.",
            "This is a separate-process, single-host loopback microbenchmark, not a cross-host, Kubernetes, Mininet, production-trace, TLS, or service-mesh evaluation.",
            "AHISF selection is based on per-workload measured candidate profiles. Runtime orchestration and live production canary rollout are not implemented.",
            "AHISF decision windows are built from disjoint calibration trial blocks; they are independent controller observations, while the validation set remains held out.",
        ],
    }
    (out_dir / "experiment_results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    _write_summary_csv(summaries, decisions, out_dir / "summary.csv")
    return report


def _write_summary_csv(summaries, decisions, path: Path):
    rows = []
    by_workload = {d["workload"]: d for d in decisions}
    for workload, candidates in summaries.items():
        selected = by_workload[workload]["selected"]
        for candidate, values in candidates.items():
            rows.append({"workload": workload, "candidate": candidate, "ahisf_selected": candidate == selected, **values})
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("results"))
    parser.add_argument("--repetitions", type=int, default=15)
    parser.add_argument("--duration", type=float, default=3.0)
    args = parser.parse_args()
    if args.repetitions < 10:
        raise SystemExit("Use at least 10 repetitions (>=4 calibration and >=6 held-out validation)")
    run_experiment(args.out, args.repetitions, args.duration)


if __name__ == "__main__":
    main()
