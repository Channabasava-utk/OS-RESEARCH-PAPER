# AHISF File Mapping and Provenance Record

| Original Flattened File (`attachments (1)/`) | Corrected Modular Location (`AHISF_CORRECTED/`) | Description & Cryptographic Provenance |
|---|---|---|
| `controller.py` | `src/ahisf/controller.py` | AHISF Decision Engine: Contracts, Gates, Cost Function, Hysteresis |
| `protocols.py` | `src/ahisf/protocols.py` | REST/JSON & gRPC/Protobuf Process-Separated Adapters |
| `benchmark.py` | `src/ahisf/benchmark.py` | Open-loop harness, RSS & CPU sampler, Poisson arrival simulator |
| `__init__.py` | `src/ahisf/__init__.py` | Package initializer |
| `ahisf.proto` | `proto/ahisf.proto` | Protocol Buffers IDL specification |
| `test_controller.py` | `tests/test_controller.py` | Unit tests for Controller logic, gates, canary, hysteresis |
| `test_adapters.py` | `tests/test_adapters.py` | Integration tests verifying functional equivalence of REST and gRPC |
| `run_matched_three_policy.py` | `experiments/three_policy/run_matched_three_policy.py` | Matched 3-policy experimental runner |
| `measure_switch.py` | `experiments/rest_grpc/measure_switch.py` | Client connection lifecycle re-establishment benchmark |
| `raw_trials_1.csv` | `calibration/primary_raw_trials.csv` & `historical_data/primary_process_separated/raw_trials.csv` | Primary 90-trial baseline dataset (SHA-256: `7ef96f...`) |
| `raw_request_latencies_1.csv` | `historical_data/primary_process_separated/raw_request_latencies.csv` | 67,500 raw request observations from primary run (SHA-256: `195195...`) |
| `summary_1.csv` | `historical_data/primary_process_separated/summary.csv` | Bootstrap CI summaries for primary run (SHA-256: `db8021...`) |
| `experiment_results_1.json` | `historical_data/primary_process_separated/experiment_results.json` | Hardware and environment metadata for primary run |
| `raw_trials.csv` | `historical_data/independent_short_rerun/raw_trials.csv` | Independent short rerun dataset (SHA-256: `fe4154...`) |
| `raw_request_latencies.csv` | `historical_data/independent_short_rerun/raw_request_latencies.csv` | 2,244 raw request observations from short rerun (SHA-256: `c43c1e...`) |
| `summary.csv` | `historical_data/independent_short_rerun/summary.csv` | Bootstrap CI summaries for short rerun (SHA-256: `3b5936...`) |
| `experiment_results.json` | `historical_data/independent_short_rerun/experiment_results.json` | Metadata for short rerun |
| `switch_cost.csv` | `historical_data/independent_short_rerun/switch_cost.csv` | 40 client route switch transitions |
| `policy_window_results.csv` | `historical_data/historical_three_policy/policy_window_results.csv` | 300 window records from 10-repetition 3-policy run (SHA-256: `44ce7f...`) |
| `raw_request_latencies_2.csv` | `historical_data/historical_three_policy/raw_request_latencies.csv` | 243,000 raw request observations from 3-policy run (SHA-256: `9ae1ba...`) |
| `adaptive_decisions.csv` | `historical_data/historical_three_policy/adaptive_decisions.csv` | Decision and scoring traces per window |
| `randomized_policy_order.csv` | `historical_data/historical_three_policy/randomized_policy_order.csv` | Policy permutation order per repetition |
| `experiment_metadata.json` | `historical_data/historical_three_policy/experiment_metadata.json` | Three-policy experiment configuration metadata |
| `AHISF_Research_Manuscript...docx` | `manuscript/AHISF_Research_Manuscript_Expanded_Evaluation_and_Search_original.docx` | Original draft manuscript |
