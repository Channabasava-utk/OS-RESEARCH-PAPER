import unittest
from ahisf.controller import (
    AHISFConfig, AHISFController, InteractionContract, MetricProfile,
)


class ControllerTests(unittest.TestCase):
    def profile(self, latency=10, throughput=1000, cpu=10, memory=100, network=1, errors=0):
        return MetricProfile(latency, throughput, cpu, memory, network, errors)

    def test_semantics_gate_rejects_broker_for_rpc_contract(self):
        contract = InteractionContract(
            interaction="request_response",
            supported_candidates=("rest_json", "grpc_protobuf", "rabbitmq"),
        )
        controller = AHISFController(contract)
        result = controller.score("rabbitmq", self.profile(), 100)
        self.assertFalse(result.feasible)
        self.assertTrue(any("semantics mismatch" in x for x in result.reasons))

    def test_latency_slo_is_hard_constraint(self):
        controller = AHISFController(InteractionContract(max_p99_ms=20))
        result = controller.score("grpc_protobuf", self.profile(latency=21), 100)
        self.assertFalse(result.feasible)
        self.assertIn("p99 latency SLO violated", result.reasons)

    def test_hysteresis_requires_two_improving_windows(self):
        config = AHISFConfig(hysteresis_delta=0.05, consecutive_windows=2, min_residence_windows=0)
        controller = AHISFController(InteractionContract(), config, initial_route="rest_json")
        profiles = {
            "rest_json": self.profile(latency=40, throughput=1000, cpu=30),
            "grpc_protobuf": self.profile(latency=10, throughput=1100, cpu=20),
        }
        first = controller.decide(profiles, 500, "w1")
        self.assertEqual(first["selected"], "rest_json")
        self.assertEqual(first["status"], "pending_hysteresis")
        second = controller.decide(profiles, 500, "w2")
        self.assertEqual(second["selected"], "grpc_protobuf")
        self.assertEqual(second["status"], "switch")

    def test_security_requirement_rejects_plaintext_adapters(self):
        contract = InteractionContract(encryption_required=True)
        controller = AHISFController(contract)
        result = controller.score("grpc_protobuf", self.profile(), 100)
        self.assertFalse(result.feasible)
        self.assertIn("encryption requirement not met", result.reasons)

    def test_canary_gate_rolls_back_on_latency_regression(self):
        controller = AHISFController(InteractionContract(), initial_route="rest_json")
        result = controller.evaluate_canary(
            candidate="grpc_protobuf", candidate_p99_ms=30, candidate_error_rate=0,
            baseline_p99_ms=10, baseline_error_rate=0,
        )
        self.assertFalse(result["canary_passed"])
        self.assertEqual(result["selected"], "rest_json")
        self.assertEqual(result["status"], "rollback")


    def test_stale_telemetry_is_rejected(self):
        controller = AHISFController(InteractionContract())
        stale = self.profile()
        stale = MetricProfile(stale.p99_ms, stale.throughput_rps, stale.cpu_pct,
                              stale.memory_mb, stale.encoded_network_mb_s,
                              stale.error_rate, sample_age_s=3.0)
        result = controller.score("grpc_protobuf", stale, 100)
        self.assertFalse(result.feasible)
        self.assertIn("stale telemetry", result.reasons)

    def test_no_feasible_candidate_fails_closed(self):
        contract = InteractionContract(encryption_required=True)
        controller = AHISFController(contract, initial_route="rest_json")
        profiles = {"rest_json": self.profile(), "grpc_protobuf": self.profile()}
        result = controller.decide(profiles, 100, "no-secure-route")
        self.assertIsNone(result["selected"])
        self.assertFalse(result["route_usable"])
        self.assertEqual(result["status"], "no_feasible_candidate_fail_closed")

    def test_infeasible_current_route_is_not_protected_by_hysteresis(self):
        contract = InteractionContract(max_p99_ms=20)
        controller = AHISFController(contract, initial_route="rest_json")
        profiles = {
            "rest_json": self.profile(latency=25),
            "grpc_protobuf": self.profile(latency=10),
        }
        result = controller.decide(profiles, 100, "slo-breach")
        self.assertEqual(result["selected"], "grpc_protobuf")
        self.assertEqual(result["status"], "forced_switch_infeasible_current")

    def test_rollback_unavailable_fails_closed(self):
        controller = AHISFController(InteractionContract(), initial_route="grpc_protobuf")
        result = controller.force_safe_rollback("rest_json", "server_down", fallback_available=False)
        self.assertIsNone(result["selected"])
        self.assertFalse(result["route_usable"])
        self.assertEqual(result["status"], "rollback_unavailable_fail_closed")

    def test_safe_rollback(self):
        controller = AHISFController(InteractionContract(), initial_route="grpc_protobuf")
        result = controller.force_safe_rollback("rest_json", "error_threshold")
        self.assertEqual(result["selected"], "rest_json")
        self.assertEqual(result["status"], "rollback")


if __name__ == "__main__":
    unittest.main()
