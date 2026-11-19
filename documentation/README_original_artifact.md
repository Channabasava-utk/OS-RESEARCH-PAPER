# AHISF Process-Separated Research Artifact

This bundle accompanies the revised manuscript **“Contract-Aware Adaptive Selection Between Microservice RPC Implementations: A Structured Critical Review, AHISF Design, and Initial Process-Separated Evaluation.”**

## Scope of the evaluated prototype

The recorded benchmark evaluates two semantically compatible synchronous request-response adapters:

- REST over HTTP/1.1 with JSON and Base64 payload encoding.
- gRPC over HTTP/2 with Protocol Buffers.

Both implement the same request operation: receive a request identifier and byte payload, then return the identifier, payload length, and SHA-256 digest. Responses are checked for integrity. The benchmark launches the server as a separate operating-system process from the client. Both processes run on the same Linux host and communicate over IPv4 loopback.

Message brokers and shared-memory IPC are not drop-in candidates for this RPC interaction. They are shown as conceptual future branches in the framework architecture and are not implemented or measured in this artifact.

## AHISF prototype

`ahisf/controller.py` implements the interaction contract, candidate metadata, hard feasibility checks, normalised weighted cost function, hysteresis, minimum residence-time handling, and fail-closed/rollback decisions.

`ahisf/protocols.py` implements REST and gRPC adapters. The Protocol Buffers service definition is in `proto/ahisf.proto`; dynamic message descriptors are used by the prototype, so the `protoc` compiler is not required.

`ahisf/benchmark.py` runs open-loop trials and records scheduled-arrival latency, service round-trip latency, throughput, CPU, peak RSS, encoded application bytes, errors, and response-integrity status. The benchmark process starts and supervises each server process separately.

## Recorded experiment

The included `results_process_separated/` directory contains the run used in the manuscript:

- 2 candidate adapters × 3 workload profiles × 15 repetitions = **90 timed trials**.
- 5 calibration trials and 10 independent held-out validation trials per candidate/workload pair.
- 3-second timed trials after 100 warm-up requests.
- 67,500 completed requests across the full run; 45,000 completed held-out requests; zero observed transport or response-validation errors.
- 11 automated tests passed in the recorded test run.

The three workloads are: 64-byte steady traffic, 4-KiB bursty traffic, and 64-KiB steady traffic. The calibration-only selector selected REST for the small and large profiles and gRPC for the bursty profile. The held-out data showed no clear p99 winner for the small profile and lower gRPC p99 for the medium and large profiles. The large-profile calibration/validation mismatch is retained as a negative result and a limitation, not hidden as a successful prediction.

## Reproduction environment

The recorded run used Linux 6.18.57 (x86_64, glibc 2.41), an AMD EPYC 9V74 80-Core Processor, five CPUs available in the process-affinity set, and 5.81 GiB of host memory. Software versions: Python 3.13.5, grpcio 1.80.0, protobuf 6.33.6, requests 2.32.5, Uvicorn 0.48.0, Starlette 0.50.0, and psutil 7.2.2.

The tested endpoints use plaintext HTTP/1.1 and gRPC over HTTP/2. There is no TLS/mTLS, service mesh, Kubernetes, Mininet, cross-host network, external broker, shared-memory path, database, production trace, or live canary traffic router in this run. CPU and peak RSS are sampled separately for the client and server processes. Encoded application bytes do not represent packet-captured wire bandwidth and omit protocol headers, TLS records, retransmissions, and proxy overhead.

## Reproduction commands

Use Python 3.11 or later (the recorded environment used Python 3.13.5):

```bash
python -m venv .venv
# Linux/macOS
source .venv/bin/activate
# Windows PowerShell
# .venv\\Scripts\\Activate.ps1
pip install -r requirements.txt
```

Run the controller and adapter tests:

```bash
python -m unittest discover -s tests -v
```

Run a new process-separated experiment:

```bash
python -m ahisf.benchmark --out results_process_separated --repetitions 15 --duration 3.0
```

The command creates new measurements; exact timing values may differ from the included recorded run. The selector uses calibration trials only; the held-out trials are reserved for reporting.

## Included data

- `results_process_separated/raw_trials.csv` — one row per timed trial.
- `results_process_separated/raw_request_latencies.csv` — request-level latency observations.
- `results_process_separated/summary.csv` — aggregate workload/protocol metrics.
- `results_process_separated/experiment_results.json` — environment, method, controller choices, and run metadata.
- `figures/` — the architecture and held-out latency figures used by the manuscript.

## Limitations and next steps

This is an initial process-separated, single-host loopback evaluation. It does not validate multi-host or container-overlay behaviour, production microservices, TLS/mTLS, service-mesh overhead, live routing/canary deployment, broker selection, shared-memory IPC, or kernel-bypass techniques. The controller is a prototype decision component rather than a production data-plane router. Further work should extend the testbed to separate containers and hosts, controlled network impairment, security-enabled configurations, longer calibration horizons, trace replay, and independent replication on controlled hardware.
