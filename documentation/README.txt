AHISF EXPANDED EVALUATION + LITERATURE SEARCH REPLICATION

Main updated manuscript: AHISF_Research_Manuscript_Expanded_Evaluation_and_Search.docx

Evaluation contents:
- ahisf/: implementation controller, protocol adapters, benchmark harness
- tests/: 11 automated tests
- primary_process_separated_results/: 90-trial primary dataset (15 reps/candidate/workload; 5 calibration + 10 held-out; 3 s/trial)
- independent_short_rerun/: fresh 10-repetition/candidate/workload short rerun (4 calibration + 6 held-out; 0.25 s/trial)
- switch_cost_raw.csv / switch_cost_summary.csv: 40 alternating client route transitions
- process_separated_validation_summary.csv and paired_bootstrap_differences.csv: held-out summaries and bootstrap intervals
- selector_vs_validation_oracle.csv: calibration-selected candidate compared with held-out p99 winner
- test_log.txt: test result

Search contents:
- search_log.csv: exact query/action, source family, record identifiers, access limitations
- screening_log.csv: inclusion/exclusion decisions and reason
- extraction_sheet.csv: method/setup/metrics/findings/limits for closest studies
- references_audit.csv: identifier and record verification status
- search_methodology.txt: search date/window/queries/limits

Important interpretation:
The primary process-separated run is a 15-trial/candidate/workload dataset with ten held-out validation trials. AHISF selected REST/small, gRPC/medium, REST/large. The large workload is a negative selector result: held-out p99 favoured gRPC. The route-switch test measures client session/channel recreation and first request while servers are warm; it is not a live production data-plane cutover. Canary and rollback are unit-tested, not deployed as live traffic splitting. The profile-based selector uses measured calibration profiles for both candidates; it does not yet implement online counterfactual prediction or a live exploration budget.

Run tests:
  python -m unittest discover -s tests -v
Run a new benchmark (longer; uses same process-separated harness):
  python -m ahisf.benchmark --out new_results --repetitions 15 --duration 3.0
