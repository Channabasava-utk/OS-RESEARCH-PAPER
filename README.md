# AHISF — Adaptive Hybrid IPC Selection Framework

Research prototype and reproducibility package for comparing contract-compatible REST/JSON and gRPC/Protocol Buffers adapters, with a constrained selection controller and associated experiments.

## Evidence scope — read before citing results

The project package separates its evidence sets. Do not merge or describe them as one experiment:

1. **Primary process-separated benchmark:** historical single-host, plaintext REST/HTTP/1.1 versus gRPC/HTTP/2 results.
2. **Independent short rerun:** a separate, shorter repeat.
3. **Historical three-policy run (16 November 2026):** 300 workload-window observations across `fixed_rest`, `fixed_grpc`, and `adaptive_ahisf`, with 243,000 completed requests and zero recorded request errors.
4. **Smoke-test outputs:** short runner checks; not confirmatory results.
5. **Repetition-shift analysis:** diagnostic analysis of an earlier repetition-level performance shift.

**Critical limitation:** the historical adaptive policy was supplied the current workload phase from a deterministic schedule and used frozen calibration profiles. It was not a live telemetry-driven router, did not perform online counterfactual learning, and did not demonstrate production traffic switching, canary routing, or end-to-end rollback. The three-policy results are exploratory evidence for the stated phase-aware policy comparison, not proof that live AHISF outperforms fixed policies.

The primary benchmark was conducted on one Linux host using plaintext loopback communication. TLS/mTLS, multi-host networking, service-mesh deployment, brokers, and host-local shared-memory adapters were not evaluated.

## Repository layout

```text
src/ahisf/                  Current modular framework implementation
proto/                      Protocol Buffers service definition
tests/                      Unit and adapter tests
calibration/                Calibration inputs and runner
experiments/                Three-policy and REST/gRPC experiment runners
historical_data/             Historical raw datasets and metadata
results/                     Repetition-shift and smoke-test outputs
outputs/                     Supplied three-policy summaries and figures
analysis/                    Repetition-shift analysis script
documentation/               Literature audit, screening, search and data summaries
manuscript/                  Research manuscript artifact
provenance/                  Exact source snapshot associated with historical run
```

## Setup

Python 3.10+ is required. Linux is recommended for reproducing the benchmark.

```bash
git clone https://github.com/Channabasava-utk/OS-RESEARCH-PAPER.git
cd OS-RESEARCH-PAPER
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e .
```

Alternatively:

```bash
pip install -r requirements.txt
```

## Run tests

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

Packaging verification in the supplied working directory completed **19 tests successfully**. Re-run tests in your own environment and retain that environment-specific log.

## Run experiments

Inspect options before starting a full run:

```bash
python experiments/three_policy/run_matched_three_policy.py --help
python experiments/rest_grpc/run_interleaved_rest_grpc.py --help
python analysis/analyze_repetition_shift.py --help
```

Use a new output directory for every run. Do not overwrite supplied historical results.

## Data integrity and research integrity

Raw datasets are under `historical_data/`. Search, screening, extraction and reference-audit records are under `documentation/`. `MANIFEST.sha256` contains checksums for the packaged files (excluding the manifest itself).

Do not claim production adaptive-router validation, live telemetry-driven adaptation, systematic-review completeness, or independent double screening unless corresponding evidence is added. Do not describe smoke tests as confirmatory findings.
