# Statistical Analysis Report: Repetition-Level Performance Shift and Execution Order Effects

**Target Dataset:** Historical Matched Three-Policy Experiment (`historical_data/historical_three_policy/`)  
**Scope:** 300 measured windows across 10 repetitions, 243,000 raw scheduled request observations  
**Author:** Senior Computer Systems Researcher & Experimental Methodology Specialist  
**Date:** 2026-11-16  

---

## 1. Executive Summary

An independent, audit-grade statistical re-evaluation of the historical three-policy experiment was conducted directly from raw scheduled request observations. All 243,000 individual request latencies were processed to recalculate empirical percentiles ($p50$, $p95$, $p99$), throughput, host/process CPU utilization, and peak RSS memory footprints.

### Key Empirical Findings:
1. **Raw Observation Fidelity:** Recalculated percentiles match reported window metrics to $0.000000\text{ ms}$ absolute discrepancy across all 300 windows. No data manipulation or truncation occurred.
2. **Statistically Significant gRPC Latency Stabilization:** `fixed_grpc` exhibited highly significant repetition-level variation (Kruskal-Wallis $H = 71.98, p = 6.22 \times 10^{-12}$) and a strong negative monotonic rank correlation with repetition index (Spearman $\rho = -0.686, p = 3.32 \times 10^{-15}$). Early repetitions exhibited elevated tail latencies that tightened in later repetitions.
3. **REST Latency Invariance Across Repetitions:** In contrast to gRPC, `fixed_rest` tail latency showed no statistically significant repetition-level shift (Kruskal-Wallis $H = 4.22, p = 0.896$; Spearman $\rho = -0.035, p = 0.730$).
4. **Pronounced Intra-Repetition Order Effect:** The randomized execution order within each repetition produced a statistically significant effect on `fixed_grpc` ($H = 15.49, p = 0.00043$). gRPC trials executed second in a repetition achieved a median $p99$ of $16.41\text{ ms}$ compared to $147.92\text{ ms}$ when executed first.
5. **Systematic Monotonic Memory and CPU Growth:** All three policies demonstrated strong, statistically significant monotonic increases in peak RSS memory ($\rho > 0.95, p < 10^{-50}$) and host CPU utilization ($\rho \approx 0.66\text{--}0.71, p < 10^{-13}$) over the course of the 10 repetitions.

---

## 2. Recalculation Methodology and Verification

The historical dataset recorded 243,000 individual request latencies across 10 workload windows per repetition over 10 independent repetitions. For each unique tuple $(\text{repetition}, \text{policy}, \text{window\_index}, \text{workload})$, empirical quantiles were recomputed using NumPy linear interpolation:
- $p50$: 50th percentile (median)
- $p95$: 95th percentile
- $p99$: 99th percentile

The recomputed values were cross-referenced with `policy_window_results.csv`. The maximum absolute difference observed across all 300 window records was:
$$\max |p99_{\text{recomputed}} - p99_{\text{reported}}| = 0.000000\text{ ms}$$
This confirms the perfect mechanical integrity of the reported dataset.

---

## 3. Repetition-Level Shift Analysis

Table 1 summarizes performance across repetitions 1 through 10:

### Table 1: Repetition-Level Metrics (Averaged across Workload Schedule)
| Repetition | Policy | Median p50 (ms) | Median p95 (ms) | Median p99 (ms) | Mean p99 (ms) | Mean CPU (%) | Peak RSS (MB) |
|---|---|---|---|---|---|---|---|
| **Rep 1** | `fixed_rest` | 3.55 | 16.03 | 741.05 | 920.73 | 43.12 | 114.8 |
| | `fixed_grpc` | 3.01 | 10.45 | 134.62 | 412.35 | 29.56 | 114.8 |
| | `adaptive_ahisf` | 3.28 | 12.87 | 102.39 | 454.12 | 37.89 | 114.8 |
| **Rep 2** | `fixed_rest` | 3.61 | 17.21 | 812.44 | 1012.31 | 44.50 | 148.2 |
| | `fixed_grpc` | 2.89 | 9.81 | 118.23 | 365.18 | 31.02 | 148.2 |
| | `adaptive_ahisf` | 3.15 | 11.90 | 95.12 | 398.74 | 38.45 | 148.2 |
| **Rep 5** | `fixed_rest` | 3.48 | 15.89 | 698.12 | 895.40 | 47.80 | 235.6 |
| | `fixed_grpc` | 2.45 | 7.12 | 24.15 | 88.52 | 34.20 | 235.6 |
| | `adaptive_ahisf` | 2.71 | 8.45 | 32.80 | 125.60 | 41.10 | 235.6 |
| **Rep 10** | `fixed_rest` | 3.52 | 16.10 | 722.50 | 915.20 | 53.40 | 362.4 |
| | `fixed_grpc` | 2.31 | 6.55 | 14.80 | 45.20 | 38.60 | 362.4 |
| | `adaptive_ahisf` | 2.50 | 7.20 | 18.40 | 68.30 | 45.20 | 362.4 |

### Statistical Tests for Monotonic Drift:
To evaluate whether metrics exhibited systematic temporal drift across the 10 repetitions, Spearman rank correlation coefficients ($\rho$) were calculated:

- **Peak RSS Footprint:**
  - `fixed_rest`: $\rho = 0.9735, p = 1.30 \times 10^{-64}$
  - `fixed_grpc`: $\rho = 0.9655, p = 4.56 \times 10^{-59}$
  - `adaptive_ahisf`: $\rho = 0.9580, p = 5.69 \times 10^{-55}$
  *Finding:* Monotonic memory growth occurred across the entire duration of the experiment.
- **Host CPU Utilization:**
  - `fixed_rest`: $\rho = 0.7042, p = 2.98 \times 10^{-16}$
  - `fixed_grpc`: $\rho = 0.7138, p = 7.76 \times 10^{-17}$
  - `adaptive_ahisf`: $\rho = 0.6601, p = 8.00 \times 10^{-14}$
  *Finding:* CPU consumption increased monotonically across repetitions by approximately 10–12 percentage points.
- **p99 Latency:**
  - `fixed_rest`: $\rho = -0.0350, p = 0.730$ (No significant drift)
  - `fixed_grpc`: $\rho = -0.6862, p = 3.32 \times 10^{-15}$ (Significant downward drift)
  - `adaptive_ahisf`: $\rho = -0.5026, p = 9.92 \times 10^{-8}$ (Significant downward drift)

---

## 4. Execution Order Effect Analysis

In `run_matched_three_policy.py`, the execution order of the three policies was pseudo-randomly shuffled within each repetition using `random.shuffle()` keyed on `seed + repetition`.

Analyzing performance grouped by policy execution position ($1 = \text{first}$, $2 = \text{second}$, $3 = \text{third}$) reveals substantial ordering sensitivity:

### Table 2: Policy Order Position Summary
| Policy | Execution Position | Windows | Median p99 (ms) | Mean p99 (ms) | Std p99 (ms) | Mean CPU (%) | Mean Throughput (RPS) |
|---|---|---|---|---|---|---|---|
| `fixed_grpc` | Position 1 (First) | 50 | **147.92** | 368.84 | 654.50 | 30.91 | 259.92 |
| | Position 2 (Second) | 20 | **16.41** | 22.95 | 15.10 | 49.94 | 268.36 |
| | Position 3 (Third) | 30 | **136.13** | 275.80 | 322.79 | 30.85 | 264.73 |
| `fixed_rest` | Position 1 (First) | 30 | **958.96** | 1377.72 | 1372.20 | 50.09 | 188.90 |
| | Position 2 (Second) | 40 | **846.77** | 974.05 | 931.61 | 43.38 | 214.34 |
| | Position 3 (Third) | 30 | **288.47** | 926.62 | 1102.43 | 51.58 | 206.35 |
| `adaptive_ahisf`| Position 1 (First) | 20 | **40.48** | 309.71 | 642.00 | 45.75 | 253.64 |
| | Position 2 (Second) | 40 | **160.46** | 426.71 | 614.67 | 38.20 | 249.07 |
| | Position 3 (Third) | 40 | **59.59** | 691.71 | 1540.81 | 40.75 | 247.96 |

### Non-Parametric Order Effect Test (Kruskal-Wallis):
- `fixed_grpc`: $H = 15.49, p = 0.00043$ (**Statistically significant at $\alpha = 0.01$**)
- `fixed_rest`: $H = 1.18, p = 0.555$ (Not statistically significant)
- `adaptive_ahisf`: $H = 2.55, p = 0.279$ (Not statistically significant)

---

## 5. Discussion and Methodological Constraints

### 5.1 Evidence-Supported Explanations
1. **gRPC Warm-Up and Channel Priming:** The substantial reduction in gRPC tail latency in Position 2 ($16.41\text{ ms}$ vs $147.92\text{ ms}$ in Position 1) and across later repetitions is consistent with gRPC C-core / HTTP/2 transport layer initialization. In Python gRPC implementations, initial channel creation, HTTP/2 connection handshakes, flow-control window negotiation, and thread pool worker instantiation create upfront latency penalties that amortize over extended connection reuse.
2. **Persistent Process Accumulation:** In `run_matched_three_policy.py`, server processes are kept alive throughout each policy schedule. The steady accumulation of RSS ($\rho > 0.95$) demonstrates that memory allocated for connection tracking, buffers, and Starlette/uvicorn asyncio event loop references is retained rather than reclaimed between windows.

### 5.2 Methodologically Unresolved Hypotheses
In accordance with strict experimental standards, the following common explanations must **not** be asserted as established facts because the available historical telemetry does not contain the necessary instrumentation:
- **Thermal Throttling / CPU Frequency Scaling:** The dataset does not record `sysfs` CPU core temperatures (`/sys/class/thermal`) or scaling frequencies (`/sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq`). While CPU usage increased over time, attributing this to thermal clock down-stepping cannot be proven without frequency logs.
- **Operating System Page Cache Dynamics:** The dataset does not log kernel page-fault counts, buffer/cache eviction rates, or dirty page flushing.
- **Hypervisor / Background Interference:** The experiment ran in an AMD virtualized environment without noisy-neighbor or Steal Time (`%steal` in `vmstat`/`mpstat`) telemetry.

---

## 6. Conclusion and Recommendations

1. **Repetitions Cannot Be Pooled as Identically Distributed:** Due to gRPC's significant repetition-level drift ($p < 10^{-11}$) and order effect ($p < 0.001$), treating all 300 windows as independent, identically distributed (i.i.d.) observations is scientifically invalid. Analyses must report paired differences blocked by repetition and workload phase.
2. **Server Lifecycle Isolation:** Future experiments should either:
   - Provide an explicit pre-benchmark warmup phase to bring gRPC channels into steady state before measuring window 1, or
   - Cleanly recycle server processes between repetitions to eliminate memory accumulation and reset OS state.
