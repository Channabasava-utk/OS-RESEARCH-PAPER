# Reproducibility and run discipline

1. Start from a clean Git checkout.
2. Record OS, kernel, Python, CPU, affinity, cgroup, memory, network path and
   dependency versions.
3. Run tests and save a fresh test log.
4. Use a new output directory for every experiment.
5. Freeze workload schedule, randomization seed, calibration data, selector
   parameters and analysis plan before validation.
6. Retain failed runs and document exclusions.
7. Preserve request-level data, per-window/per-trial summaries, policy decisions,
   randomization order and metadata.
8. Re-run analysis from raw data and compare generated summaries.
9. Generate checksums after all files are final.
10. Tag the Git commit before archiving a release.

The supplied three-policy experiment is not a live telemetry adaptation test:
the adaptive policy receives the workload phase from the deterministic schedule.
