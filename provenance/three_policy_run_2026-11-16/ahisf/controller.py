"""Contract-aware Adaptive Hybrid IPC Selection Framework (AHISF) prototype."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Mapping, Sequence
import math


@dataclass(frozen=True)
class InteractionContract:
    interaction: str = "request_response"
    topology: str = "network"
    authentication: str = "none"
    encryption_required: bool = False
    required_delivery: str = "at_most_once"
    max_p99_ms: float = 100.0
    max_error_rate: float = 0.01
    supported_candidates: tuple[str, ...] = ("rest_json", "grpc_protobuf")


@dataclass(frozen=True)
class CandidatePolicy:
    name: str
    interaction: str
    topology: str
    authentication_configured: bool
    encryption_configured: bool
    delivery: str
    operational_cost: float


POLICIES: dict[str, CandidatePolicy] = {
    # The benchmark adapters use plaintext loopback without authentication.
    # Protocols may support TLS/auth in other configurations; this prototype does not enable it.
    "rest_json": CandidatePolicy("rest_json", "request_response", "network", False, False, "at_most_once", 0.15),
    "grpc_protobuf": CandidatePolicy("grpc_protobuf", "request_response", "network", False, False, "at_most_once", 0.35),
    # These candidates are policy examples only; no broker/SHM adapter is included yet.
    "rabbitmq": CandidatePolicy("rabbitmq", "queue", "network", False, False, "at_least_once", 0.45),
    "kafka": CandidatePolicy("kafka", "event_log", "network", False, False, "replayable", 0.70),
    "shared_memory": CandidatePolicy("shared_memory", "shared_memory", "host_local", False, False, "at_most_once", 0.80),
}


@dataclass(frozen=True)
class ScoreWeights:
    latency: float = 0.40
    capacity: float = 0.20
    cpu: float = 0.20
    memory: float = 0.05
    network: float = 0.10
    operations: float = 0.05

    def validate(self) -> None:
        vals = asdict(self)
        if any(v < 0 for v in vals.values()) or abs(sum(vals.values()) - 1.0) > 1e-9:
            raise ValueError("AHISF weights must be non-negative and sum to 1")


@dataclass(frozen=True)
class MetricProfile:
    p99_ms: float
    throughput_rps: float
    cpu_pct: float
    memory_mb: float
    encoded_network_mb_s: float
    error_rate: float = 0.0
    sample_age_s: float = 0.0


@dataclass(frozen=True)
class ScoreResult:
    candidate: str
    feasible: bool
    score: float | None
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class AHISFConfig:
    max_p99_ms: float = 100.0
    max_error_rate: float = 0.01
    cpu_budget_pct: float = 90.0
    memory_budget_mb: float = 512.0
    network_budget_mb_s: float = 100.0
    weights: ScoreWeights = ScoreWeights()
    hysteresis_delta: float = 0.05
    consecutive_windows: int = 2
    min_residence_windows: int = 2
    max_metric_age_s: float = 2.0


class AHISFController:
    """Scores only contract-compatible candidates and applies hysteresis.

    This research prototype performs selection at decision-window granularity.
    It never dynamically changes an application's message semantics.
    """
    def __init__(self, contract: InteractionContract, config: AHISFConfig | None = None,
                 initial_route: str = "rest_json"):
        self.contract = contract
        self.config = config or AHISFConfig()
        self.config.weights.validate()
        self.current_route: str | None = initial_route
        self._better_streak = 0
        self._residence = 0
        self.last_decision: dict | None = None

    def feasible(self, candidate: str, metric: MetricProfile) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        policy = POLICIES.get(candidate)
        if candidate not in self.contract.supported_candidates:
            reasons.append("not declared in service contract")
        if policy is None:
            reasons.append("unknown candidate")
        else:
            if not (0.0 <= policy.operational_cost <= 1.0):
                reasons.append("operational cost score must be within [0, 1]")
            if policy.interaction != self.contract.interaction:
                reasons.append("interaction semantics mismatch")
            if self.contract.topology == "host_local" and policy.topology != "host_local":
                reasons.append("topology mismatch: host-local interaction")
            if self.contract.topology == "network" and policy.topology == "host_local":
                reasons.append("topology mismatch: host-local candidate on network edge")
            if self.contract.authentication != "none" and not policy.authentication_configured:
                reasons.append("authentication requirement not met")
            if self.contract.encryption_required and not policy.encryption_configured:
                reasons.append("encryption requirement not met")
            # Delivery modes are treated as explicit contract values, not a simple
            # strength hierarchy: at-most-once and at-least-once have different semantics.
            if policy.delivery != self.contract.required_delivery:
                reasons.append("delivery semantics mismatch")
        numeric_metrics = (metric.p99_ms, metric.throughput_rps, metric.cpu_pct,
                           metric.memory_mb, metric.encoded_network_mb_s,
                           metric.error_rate, metric.sample_age_s)
        if not all(math.isfinite(v) for v in numeric_metrics):
            reasons.append("invalid or non-finite telemetry")
        if metric.sample_age_s > self.config.max_metric_age_s:
            reasons.append("stale telemetry")
        if metric.p99_ms > min(self.contract.max_p99_ms, self.config.max_p99_ms):
            reasons.append("p99 latency SLO violated")
        if metric.error_rate > min(self.contract.max_error_rate, self.config.max_error_rate):
            reasons.append("error-rate SLO violated")
        if metric.cpu_pct > self.config.cpu_budget_pct:
            reasons.append("CPU budget exceeded")
        if metric.memory_mb > self.config.memory_budget_mb:
            reasons.append("memory budget exceeded")
        if metric.encoded_network_mb_s > self.config.network_budget_mb_s:
            reasons.append("network budget exceeded")
        if min(metric.p99_ms, metric.throughput_rps, metric.cpu_pct, metric.memory_mb,
               metric.encoded_network_mb_s, metric.error_rate, metric.sample_age_s) < 0:
            reasons.append("negative telemetry value")
        return not reasons, reasons

    def score(self, candidate: str, metric: MetricProfile, demand_rps: float) -> ScoreResult:
        ok, reasons = self.feasible(candidate, metric)
        if not ok:
            return ScoreResult(candidate, False, None, tuple(reasons))
        policy = POLICIES[candidate]
        w = self.config.weights
        q_shortfall = max(0.0, demand_rps - metric.throughput_rps) / max(demand_rps, 1e-9)
        cost = (
            w.latency * (metric.p99_ms / self.config.max_p99_ms)
            + w.capacity * q_shortfall
            + w.cpu * (metric.cpu_pct / self.config.cpu_budget_pct)
            + w.memory * (metric.memory_mb / self.config.memory_budget_mb)
            + w.network * (metric.encoded_network_mb_s / self.config.network_budget_mb_s)
            + w.operations * policy.operational_cost
        )
        return ScoreResult(candidate, True, float(cost), ())

    def decide(self, profiles: Mapping[str, MetricProfile], demand_rps: float,
               window_id: str | int | None = None) -> dict:
        scored = [self.score(name, metric, demand_rps) for name, metric in profiles.items()]
        feasible = [s for s in scored if s.feasible]
        if not feasible:
            # Do not continue dispatching to a route that may violate a hard constraint.
            # The last route is retained only as state for diagnosis, not as a usable route.
            self._residence += 1
            result = {
                "selected": None,
                "last_route": self.current_route,
                "route_usable": False,
                "changed": False,
                "status": "no_feasible_candidate_fail_closed",
                "scores": [asdict(s) for s in scored],
                "window_id": window_id,
            }
            self.last_decision = result
            return result
        best = min(feasible, key=lambda s: s.score)
        current_profile = profiles.get(self.current_route) if self.current_route else None
        current_score = self.score(self.current_route, current_profile, demand_rps) if current_profile and self.current_route else None
        changed = False
        status = "retain_current"
        # A currently infeasible route must not be protected by hysteresis. The caller
        # still performs a safe cutover (drain in-flight work, preserve idempotency keys).
        if current_score is None or not current_score.feasible:
            self.current_route = best.candidate
            self._better_streak = 0
            self._residence = 0
            changed = True
            status = "forced_switch_infeasible_current"
            result = {
                "selected": self.current_route,
                "route_usable": True,
                "changed": True,
                "status": status,
                "best_candidate": best.candidate,
                "best_score": best.score,
                "scores": [asdict(s) for s in scored],
                "window_id": window_id,
            }
            self.last_decision = result
            self._residence += 1
            return result
        if best.candidate != self.current_route:
            threshold_ok = (current_score is None or not current_score.feasible or
                            best.score < (1.0 - self.config.hysteresis_delta) * current_score.score)
            if threshold_ok and self._residence >= self.config.min_residence_windows:
                self._better_streak += 1
            else:
                self._better_streak = 0
            if self._better_streak >= self.config.consecutive_windows:
                self.current_route = best.candidate
                self._better_streak = 0
                self._residence = 0
                changed = True
                status = "switch"
            else:
                status = "pending_hysteresis"
        else:
            self._better_streak = 0
        self._residence += 1
        result = {
            "selected": self.current_route,
            "route_usable": self.current_route is not None,
            "changed": changed,
            "status": status,
            "best_candidate": best.candidate,
            "best_score": best.score,
            "scores": [asdict(s) for s in scored],
            "window_id": window_id,
        }
        self.last_decision = result
        return result

    def evaluate_canary(self, candidate: str, candidate_p99_ms: float, candidate_error_rate: float,
                        baseline_p99_ms: float, baseline_error_rate: float,
                        max_latency_regression: float = 1.10) -> dict:
        """Apply a metric gate to a candidate before committing a staged rollout.

        The prototype evaluates the canary decision; it does not itself proxy or
        split live application traffic. A production adapter must implement routing.
        """
        reasons = []
        if candidate_p99_ms > self.contract.max_p99_ms:
            reasons.append("candidate violates p99 SLO")
        if candidate_error_rate > self.contract.max_error_rate:
            reasons.append("candidate violates error-rate SLO")
        if candidate_error_rate > baseline_error_rate + 0.002:
            reasons.append("candidate error rate regressed beyond 0.2 percentage points")
        if candidate_p99_ms > baseline_p99_ms * max_latency_regression:
            reasons.append("candidate p99 regressed beyond canary tolerance")
        if reasons:
            fallback = self.force_safe_rollback(self.current_route, reason="; ".join(reasons))
            return {**fallback, "candidate": candidate, "canary_passed": False, "reasons": reasons}
        self.current_route = candidate
        self._better_streak = 0
        self._residence = 0
        return {"selected": candidate, "changed": True, "status": "canary_commit",
                "candidate": candidate, "canary_passed": True, "reasons": []}

    def force_safe_rollback(self, fallback: str = "rest_json", reason: str = "safety_trigger",
                            fallback_available: bool = True) -> dict:
        if fallback not in self.contract.supported_candidates:
            fallback_available = False
        if not fallback_available:
            self.current_route = None
            self._better_streak = 0
            self._residence = 0
            self.last_decision = {"selected": None, "route_usable": False, "changed": False,
                                  "status": "rollback_unavailable_fail_closed", "reason": reason}
            return self.last_decision
        self.current_route = fallback
        self._better_streak = 0
        self._residence = 0
        self.last_decision = {"selected": fallback, "route_usable": True, "changed": True,
                              "status": "rollback", "reason": reason}
        return self.last_decision
