import hashlib
import os
import unittest
from ahisf.controller import (
    AHISFConfig, AHISFController, InteractionContract, MetricProfile, ScoreWeights
)
from ahisf.protocols import RunningAdapter


class EdgeCaseAndSafetyTests(unittest.TestCase):
    def profile(self, latency=10.0, throughput=1000.0, cpu=10.0, memory=100.0, network=1.0, error_rate=0.0):
        return MetricProfile(
            p99_ms=latency,
            throughput_rps=throughput,
            cpu_pct=cpu,
            memory_mb=memory,
            encoded_network_mb_s=network,
            error_rate=error_rate
        )

    def test_error_rate_slo_is_hard_constraint(self):
        contract = InteractionContract(max_error_rate=0.005)
        controller = AHISFController(contract)
        res_ok = controller.score("grpc_protobuf", self.profile(error_rate=0.002), 100)
        self.assertTrue(res_ok.feasible)
        res_bad = controller.score("grpc_protobuf", self.profile(error_rate=0.01), 100)
        self.assertFalse(res_bad.feasible)
        self.assertIn("error-rate SLO violated", res_bad.reasons)

    def test_minimum_residence_time_prevents_premature_switching(self):
        # min_residence_windows = 3, consecutive_windows = 2
        config = AHISFConfig(hysteresis_delta=0.05, consecutive_windows=2, min_residence_windows=3)
        controller = AHISFController(InteractionContract(), config, initial_route="rest_json")
        profiles = {
            "rest_json": self.profile(latency=50.0, throughput=1000.0, cpu=40.0),
            "grpc_protobuf": self.profile(latency=10.0, throughput=1200.0, cpu=20.0),
        }
        # Window 1: residence 0 -> 1 (< 3)
        w1 = controller.decide(profiles, 500, "w1")
        self.assertEqual(w1["selected"], "rest_json")
        self.assertEqual(w1["status"], "pending_hysteresis")

        # Window 2: residence 1 -> 2 (< 3)
        w2 = controller.decide(profiles, 500, "w2")
        self.assertEqual(w2["selected"], "rest_json")
        self.assertEqual(w2["status"], "pending_hysteresis")

        # Window 3: residence 2 -> 3 (< 3)
        w3 = controller.decide(profiles, 500, "w3")
        self.assertEqual(w3["selected"], "rest_json")
        self.assertEqual(w3["status"], "pending_hysteresis")

        # Window 4: residence 3 >= 3, streak becomes 1 (< 2) -> 4
        w4 = controller.decide(profiles, 500, "w4")
        self.assertEqual(w4["selected"], "rest_json")
        self.assertEqual(w4["status"], "pending_hysteresis")

        # Window 5: residence 4 >= 3, streak becomes 2 (>= 2) -> switch!
        w5 = controller.decide(profiles, 500, "w5")
        self.assertEqual(w5["selected"], "grpc_protobuf")
        self.assertEqual(w5["status"], "switch")

    def test_sub_hysteresis_improvement_rejected(self):
        # 5% hysteresis required; offer only ~2% score improvement
        config = AHISFConfig(hysteresis_delta=0.05, consecutive_windows=2, min_residence_windows=0)
        controller = AHISFController(InteractionContract(), config, initial_route="rest_json")
        profiles = {
            "rest_json": self.profile(latency=20.0, throughput=1000.0, cpu=20.0),
            "grpc_protobuf": self.profile(latency=19.8, throughput=1005.0, cpu=20.0),
        }
        d1 = controller.decide(profiles, 500, "w1")
        self.assertEqual(d1["selected"], "rest_json")
        self.assertEqual(d1["status"], "retain_current")
        d2 = controller.decide(profiles, 500, "w2")
        self.assertEqual(d2["selected"], "rest_json")
        self.assertEqual(d2["status"], "retain_current")

    def test_zero_throughput_handling_avoids_division_by_zero(self):
        controller = AHISFController(InteractionContract())
        res = controller.score("grpc_protobuf", self.profile(throughput=0.0), 100)
        self.assertTrue(res.feasible)
        self.assertIsNotNone(res.score)
        self.assertTrue(0.0 <= res.score <= 1.0)

    def test_empty_candidate_profiles_fails_closed(self):
        controller = AHISFController(InteractionContract(), initial_route="rest_json")
        res = controller.decide({}, 100, "empty_window")
        self.assertIsNone(res["selected"])
        self.assertFalse(res["route_usable"])
        self.assertEqual(res["status"], "no_feasible_candidate_fail_closed")

    def test_weights_validation_rejects_invalid_configurations(self):
        with self.assertRaises(ValueError):
            w = ScoreWeights(latency=0.5, capacity=0.6)  # sum = 1.1 != 1.0
            w.validate()
        with self.assertRaises(ValueError):
            w = ScoreWeights(latency=-0.1, capacity=1.1, cpu=0, memory=0, network=0, operations=0)
            w.validate()

    def test_topology_gate_rejects_host_local_candidate_on_network(self):
        contract = InteractionContract(topology="network", supported_candidates=("rest_json", "shared_memory"))
        controller = AHISFController(contract)
        res = controller.score("shared_memory", self.profile(), 100)
        self.assertFalse(res.feasible)
        self.assertTrue(any("topology mismatch" in r for r in res.reasons))

    def test_adapter_payload_boundary_equivalence(self):
        test_payloads = [
            b"",                                # 0-byte empty payload
            b"\x00",                            # 1-byte null byte
            b"Hello AHISF Wire Equivalence!",  # ASCII text
            os.urandom(64),                     # 64-byte random binary
            os.urandom(4096),                   # 4-KiB random binary
        ]
        with RunningAdapter("rest_json") as rest, RunningAdapter("grpc_protobuf") as grpc:
            rest_client = rest.client_factory()
            grpc_client = grpc.client_factory()
            try:
                for idx, payload in enumerate(test_payloads):
                    rest_resp = rest_client.call(idx + 1, payload)
                    grpc_resp = grpc_client.call(idx + 1, payload)
                    expected_sha = hashlib.sha256(payload).digest()

                    self.assertEqual(rest_resp[0], idx + 1)
                    self.assertEqual(grpc_resp[0], idx + 1)
                    self.assertEqual(rest_resp[1], len(payload))
                    self.assertEqual(grpc_resp[1], len(payload))
                    self.assertEqual(rest_resp[2], expected_sha)
                    self.assertEqual(grpc_resp[2], expected_sha)
            finally:
                rest_client.close()
                grpc_client.close()


if __name__ == "__main__":
    unittest.main()
