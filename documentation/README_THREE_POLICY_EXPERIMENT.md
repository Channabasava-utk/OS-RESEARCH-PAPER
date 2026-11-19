# AHISF matched three-policy experiment

## What this adds
`run_matched_three_policy.py` adds a repeated, randomized, matched comparison of fixed REST, fixed gRPC, and adaptive AHISF. It reuses the existing separate-process REST/JSON and gRPC/Protobuf adapters and the original primary calibration dataset.

## Important interpretation
The adaptive policy is **phase-aware and calibration-profile based**: the benchmark schedule supplies the current workload phase, and AHISF selects using profiles frozen from primary dataset repetitions 1–5. This tests the controller's policy response and hysteresis under a changing, known workload schedule. It is **not** a live telemetry-driven production router or online learner.

The schedule has two windows per phase so the configured two-consecutive-window hysteresis rule can trigger. It returns to earlier phases to test reverse transitions. Each policy receives the same per-repetition/per-window random seed, and policy execution order is randomized within each repetition.

Timed window latency does not include client/session/channel recreation cost. Use `measure_switch.py` separately and describe it as client lifecycle transition overhead, not production live cutover latency.

## Environment and setup
Use Linux/Ubuntu, Python 3.10+, and the same machine for all policies. Avoid CPU-intensive background work.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m unittest discover -s tests -v
```

Expected existing test baseline: 11 tests pass.

## Confirmatory run
Default planned run: 10 independent schedule repetitions, 3-second windows, 10 workload windows per schedule, and three policies.

```bash
python run_matched_three_policy.py --repetitions 10 --duration 3.0 --out results_three_policy
```

This is a substantial run; allow ample time and prevent the machine from sleeping. The script prints progress after each measured window. The script refuses fewer than 10 repetitions or windows shorter than 1 second.

## Outputs
- `policy_window_results.csv`: one row per policy × repetition × workload window
- `raw_request_latencies.csv`: request-level scheduled latency observations
- `adaptive_decisions.csv`: AHISF decision, route and score details
- `randomized_policy_order.csv`: execution order for each repetition
- `experiment_metadata.json`: schedule, calibration hash, seeds, environment and limitations

## Analysis rules
- Treat repeated schedules/windows as the independent units for uncertainty, not each request as independent.
- Report per-window distributions and paired policy differences by repetition/workload phase.
- Preserve failed requests; do not silently drop errors.
- Do not pool these results with the earlier 90-trial dataset.
- Do not claim AHISF superiority unless new results support it.
- Calibration data are fixed before the new experiment; do not tune weights or thresholds using confirmatory results.

## Reproducibility
Archive this source tree, exact command, environment details, output files and SHA-256 hashes. Record the Git commit or ZIP hash actually used.
