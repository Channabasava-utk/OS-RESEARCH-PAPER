import argparse
import csv
import hashlib
import statistics
import time
from pathlib import Path
from ahisf.protocols import RunningAdapter

def main():
    parser = argparse.ArgumentParser(description="Measure client session/channel lifecycle switch cost.")
    parser.add_argument("--reps", type=int, default=40, help="Number of alternating switches")
    parser.add_argument("--out", type=str, default="switch_cost.csv", help="Output CSV path")
    args = parser.parse_args()

    PAYLOAD = b'AHISF-switch-probe-64B-payload-'.ljust(64, b'x')
    REPS = args.reps
    rows = []
    with RunningAdapter('rest_json') as rest, RunningAdapter('grpc_protobuf') as grpc:
        adapters = {'rest_json': rest, 'grpc_protobuf': grpc}
        clients = {k: v.client_factory() for k, v in adapters.items()}
        # Warm both persistent channels/sessions before measuring steady-state.
        for k, c in clients.items():
            for i in range(10):
                rid, n, d, _, _ = c.call(i, PAYLOAD)
                assert rid == i and n == len(PAYLOAD) and d == hashlib.sha256(PAYLOAD).digest()
        steady = {}
        for k, c in clients.items():
            samples = []
            for i in range(100):
                t = time.perf_counter_ns()
                c.call(1000 + i, PAYLOAD)
                samples.append((time.perf_counter_ns() - t) / 1e6)
            steady[k] = statistics.median(samples)
        active = 'rest_json'
        for i in range(REPS):
            target = 'grpc_protobuf' if active == 'rest_json' else 'rest_json'
            t0 = time.perf_counter_ns()
            clients[active].close()
            clients[target] = adapters[target].client_factory()
            rid, n, d, _, _ = clients[target].call(10000 + i, PAYLOAD)
            elapsed = (time.perf_counter_ns() - t0) / 1e6
            assert rid == 10000 + i and n == len(PAYLOAD) and d == hashlib.sha256(PAYLOAD).digest()
            rows.append({
                'switch_index': i + 1,
                'from': active,
                'to': target,
                'first_request_after_switch_ms': elapsed,
                'steady_median_ms': steady[target],
                'incremental_switch_overhead_ms': elapsed - steady[target],
                'response_validated': True
            })
            active = target
        for c in clients.values():
            c.close()

    out = Path(args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote switch measurements to {out}")

    for k in ['rest_json', 'grpc_protobuf']:
        a = [r['first_request_after_switch_ms'] for r in rows if r['to'] == k]
        b = [r['incremental_switch_overhead_ms'] for r in rows if r['to'] == k]
        print(k, 'n', len(a), 'first-call median ms', statistics.median(a),
              'p95', sorted(a)[int(.95 * (len(a) - 1))],
              'incremental median ms', statistics.median(b))

if __name__ == "__main__":
    main()
