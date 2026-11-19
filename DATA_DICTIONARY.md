# Data and result guide

| Location | Contents | Interpretation |
|---|---|---|
| `calibration/` | Calibration trials and calibration runner | Calibration inputs |
| `historical_data/primary_process_separated/` | Primary raw trials, request latencies, summary and metadata | Original process-separated REST/gRPC benchmark |
| `historical_data/independent_short_rerun/` | Short rerun data, summary and switch costs | Separate short repeat |
| `historical_data/historical_three_policy/` | Historical policy-window data and decision traces | Historical three-policy evidence in corrected archive |
| `outputs/results_three_policy/` | 300-window run, request latencies, adaptive decisions, policy order and metadata | Supplied run dated 16 Nov 2026 |
| `outputs/` | Aggregated policy summaries and generated plots | Derived from supplied three-policy run |
| `results/repetition_shift/` | Shift summaries, tests, report and figures | Diagnostic analysis of repetition-level drift |
| `results/smoke_three_policy/` | Small smoke-test data | Runner check only; not confirmatory |
| `documentation/` | Search, screening, extraction and reference audit files | Review provenance; inspect verification statuses before citing |

The 243,000 request observations are nested within 300 workload windows and
10 repetitions. They are not 243,000 independent experimental units. Use the
workload window or repetition/block as specified by the analysis design.
