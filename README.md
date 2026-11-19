# AHISF — Adaptive Hybrid IPC Selection Framework

Research prototype and reproducibility package for comparing contract-compatible
REST/JSON and gRPC/Protocol Buffers adapters, with a constrained selection
controller and associated experiments.

## Evidence scope — read before citing results

The repository contains distinct evidence sets. They must not be merged or
described as one experiment:

1. **Primary process-separated benchmark:** historical single-host, plaintext
   REST/HTTP/1.1 versus gRPC/HTTP/2 results.
2. **Independent short rerun:** a separate, shorter repeat.
3. **Historical three-policy run (16 November 2026):** 300 workload-window
   observations across `fixed_rest`, `fixed_grpc`, and `adaptive_ahisf`, with
   243,000 completed requests and zero recorded request errors.
4. **Smoke-test outputs:** short checks of the runner; not confirmatory results.
5. **Repetition-shift analysis:** diagnostic analysis of the earlier
   repetition-level performance shift.

**Critical limitation:** the historical adaptive policy was supplied the current
workload phase from the deterministic schedule and used frozen calibration
profiles. It was not a live telemetry-driven router, did not perform online
counterfactual learning, and did not demonstrate production traffic switching,
canary routing, or end-to-end rollback. The three-policy results are exploratory
evidence for the stated phase-aware policy comparison, not proof that live AHISF
outperforms fixed policies.

The primary benchmark was conducted on one Linux host using plaintext loopback
communication. TLS/mTLS, multi-host networking, service-mesh deployment,
brokers, and host-local shared-memory adapters were not evaluated.

## Repository layout

```text
src/ahisf/                  Current modular framework implementation
proto/                      Protocol Buffers service definition
tests/                      Unit and adapter tests
calibration/                Calibration inputs and calibration runner
experiments/                Three-policy and REST/gRPC experiment runners
historical_data/             Historical raw datasets and metadata
results/                     Repetition-shift and smoke-test outputs
outputs/                     Supplied three-policy outputs, summaries and figures
analysis/                    Repetition-shift analysis script
documentation/               Literature audit, screening, search and data summaries
manuscript/                  Supplied manuscript artifact
provenance/                  Exact source snapshot associated with historical run
```

## Setup on Ubuntu

```bash
git clone <YOUR_GITHUB_REPOSITORY_URL>
cd <REPOSITORY_DIRECTORY>
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e .
```

Or install dependencies without editable package installation:

```bash
pip install -r requirements.txt
```

Python 3.10+ is required. Linux is recommended for reproducing the benchmark.

## Run tests

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

The supplied historical test log records 11 passing tests under Python 3.11.18.
The merged current test suite contains 19 tests; it was run from the merged
checkout with `PYTHONPATH=src` and all 19 passed in the packaging verification.
Re-run tests on your own machine and retain that log as the environment-specific
result. An old test log is not proof that a different environment passed.

## Run experiment scripts

Inspect available options before starting a full run:

```bash
python experiments/three_policy/run_matched_three_policy.py --help
python experiments/rest_grpc/run_interleaved_rest_grpc.py --help
```

For repetition-shift analysis:

```bash
python analysis/analyze_repetition_shift.py --help
```

Use a new output directory for every run. Do not overwrite the supplied results.

## Supplied historical three-policy results

The supplied run is stored in `outputs/results_three_policy/`; derived summaries
and plots are under `outputs/`. The exact runner and framework source snapshot
associated with the 16 November 2026 run are preserved in
`provenance/three_policy_run_2026-11-16/`.

The metadata records the environment, random seed, workload schedule,
calibration hash, policy-order randomization, and limitations. Preserve these
files as historical evidence; new experiments must be stored separately.

## Data integrity and research integrity

Raw datasets are under `historical_data/`. Search, screening, extraction and
reference-audit records are under `documentation/`. `MANIFEST.sha256` contains
checksums for all repository files except the manifest itself.

Do not claim production adaptive-router validation, live telemetry-driven
adaptation, systematic-review completeness, or independent double screening
unless the corresponding evidence is actually added. Do not describe smoke
tests as confirmatory findings.
