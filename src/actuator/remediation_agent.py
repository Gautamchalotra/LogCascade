"""Autonomous remediation agent for LogCascade.

Observes anomaly alerts and failure cascade predictions, applies deterministic
decision policies, verifies safety constraints with SafetyGuard, and triggers
remediation actions against Kubernetes or Service Mesh simulation environments.
"""
from __future__ import annotations

import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from src.actuator.k8s_client import SimulatedK8sClient
from src.actuator.safety_guard import SafetyDecision, SafetyGuard
from src.actuator.service_mesh_client import SimulatedServiceMeshClient
from src.common.logging_utils import get_logger
from src.common.schemas import Alert

log = get_logger("remediation_agent")


@dataclass
class RemediationEvent:
    id: str
    timestamp: float
    target_component: str
    action: str
    trigger_severity: str
    cascade_level: str
    blast_radius: float
    safety_approved: bool
    safety_reason: str
    execution_status: str  # "APPLIED" | "BLOCKED_BY_SAFETY" | "FAILED"
    execution_details: dict[str, Any] = field(default_factory=dict)
    verification_status: str = "PENDING_VERIFICATION"  # "PENDING_VERIFICATION" | "RESOLVED" | "ESCALATED"
    resolved_at: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RemediationAgent:
    """Autonomous agent that evaluates incidents and takes safe remediation actions."""

    def __init__(
        self,
        mode: str = "simulation",
        auto_remediate: bool = True,
        verification_window_s: float = 60.0,
        safety_guard: Optional[SafetyGuard] = None,
        k8s_client: Optional[SimulatedK8sClient] = None,
        mesh_client: Optional[SimulatedServiceMeshClient] = None,
        max_history: int = 500,
    ):
        self.mode = mode
        self.auto_remediate = auto_remediate
        self.verification_window_s = verification_window_s
        self.guard = safety_guard or SafetyGuard()
        self.k8s = k8s_client or SimulatedK8sClient(mode=mode)
        self.mesh = mesh_client or SimulatedServiceMeshClient(mode=mode)
        self.history: deque[RemediationEvent] = deque(maxlen=max_history)
        self._active_verifications: dict[str, RemediationEvent] = {}

    @classmethod
    def from_config(cls, cfg: dict) -> "RemediationAgent":
        rc = cfg.get("remediation", {})
        sc = rc.get("safety", {})
        guard = SafetyGuard(
            cooldown_s=sc.get("cooldown_s", 60.0),
            max_simultaneous=sc.get("max_simultaneous", 2),
            max_blast_radius=sc.get("max_blast_radius", 0.75),
            allowlist=sc.get("allowlist", ["*"]),
            allowed_actions=sc.get("allowed_actions"),
        )
        mode = rc.get("mode", "simulation")
        return cls(
            mode=mode,
            auto_remediate=rc.get("auto_remediate", True),
            verification_window_s=rc.get("verification_window_s", 60.0),
            safety_guard=guard,
            k8s_client=SimulatedK8sClient(mode=mode),
            mesh_client=SimulatedServiceMeshClient(mode=mode),
        )

    def _select_action(self, alert: Alert, cascade_state: dict) -> Optional[str]:
        """Deterministic policy selecting appropriate remediation action."""
        level = alert.cascade_level or "none"
        sev = alert.severity

        # 1. Imminent failure cascade: multiple services threatened
        if level == "imminent":
            # If multiple components at risk, apply circuit breaker to isolate propagation
            if alert.at_risk and len(alert.at_risk) > 1:
                return "circuit_break"
            # Otherwise restart or scale the root cause
            return "restart_pod"

        # 2. Critical anomaly on component
        if sev == "critical":
            # Very high reconstruction error usually signifies process crash/deadlock
            if alert.score >= alert.threshold * 2.0:
                return "restart_pod"
            # Otherwise scale up to absorb load
            return "scale_deployment"

        # 3. Watch level on cascade
        if level == "watch":
            return "rate_limit"

        return None

    def process_alert(self, alert: Alert, cascade_state: dict) -> Optional[RemediationEvent]:
        """Processes an incoming alert and autonomously triggers safe remediation if indicated."""
        if not self.auto_remediate:
            return None

        action = self._select_action(alert, cascade_state)
        if not action:
            return None

        # Calculate estimated blast radius
        active_comps = cascade_state.get("active", [])
        known_count = max(len(self.k8s._deployments), 1)
        blast_radius = len(active_comps) / known_count if active_comps else 0.0

        target = alert.component
        now = alert.ts if alert.ts > 0 else time.time()

        # Evaluate safety constraints
        safety: SafetyDecision = self.guard.evaluate(
            action=action,
            component=target,
            blast_radius=blast_radius,
            current_ts=now,
        )

        event_id = f"REM-{uuid.uuid4().hex[:8].upper()}"
        if not safety.approved:
            event = RemediationEvent(
                id=event_id,
                timestamp=now,
                target_component=target,
                action=action,
                trigger_severity=alert.severity,
                cascade_level=alert.cascade_level,
                blast_radius=round(blast_radius, 4),
                safety_approved=False,
                safety_reason=safety.reason,
                execution_status="BLOCKED_BY_SAFETY",
            )
            self.history.append(event)
            log.warning("[remediation-blocked] Action %s on %s rejected: %s", action, target, safety.reason)
            return event

        # Safety approved: execute action in simulation/real environment
        self.guard.record_action_started(target, now)
        execution_details = self._execute_action(action, target)

        event = RemediationEvent(
            id=event_id,
            timestamp=now,
            target_component=target,
            action=action,
            trigger_severity=alert.severity,
            cascade_level=alert.cascade_level,
            blast_radius=round(blast_radius, 4),
            safety_approved=True,
            safety_reason=safety.reason,
            execution_status="APPLIED",
            execution_details=execution_details,
            verification_status="PENDING_VERIFICATION",
        )
        self.history.append(event)
        self._active_verifications[target] = event
        log.info("[remediation-applied] Autonomous action %s executed on %s (%s)", action, target, event_id)
        return event

    def trigger_manual_action(self, action: str, target: str, reason: str = "manual") -> RemediationEvent:
        """Allows manual triggering of remediation with safety enforcement."""
        now = time.time()
        safety = self.guard.evaluate(action=action, component=target, blast_radius=0.0, current_ts=now)
        event_id = f"MAN-{uuid.uuid4().hex[:8].upper()}"

        if not safety.approved:
            event = RemediationEvent(
                id=event_id,
                timestamp=now,
                target_component=target,
                action=action,
                trigger_severity="manual",
                cascade_level="none",
                blast_radius=0.0,
                safety_approved=False,
                safety_reason=safety.reason,
                execution_status="BLOCKED_BY_SAFETY",
            )
            self.history.append(event)
            return event

        self.guard.record_action_started(target, now)
        details = self._execute_action(action, target)
        event = RemediationEvent(
            id=event_id,
            timestamp=now,
            target_component=target,
            action=action,
            trigger_severity="manual",
            cascade_level="none",
            blast_radius=0.0,
            safety_approved=True,
            safety_reason="Manual invocation passed safety checks",
            execution_status="APPLIED",
            execution_details=details,
            verification_status="RESOLVED",
            resolved_at=now,
        )
        self.history.append(event)
        self.guard.record_action_completed(target)
        return event

    def _execute_action(self, action: str, target: str) -> dict[str, Any]:
        """Dispatches action to the appropriate simulation client."""
        if action == "restart_pod":
            return self.k8s.restart_pod(target)
        elif action == "scale_deployment":
            return self.k8s.scale_deployment(target)
        elif action == "drain_node":
            return self.k8s.drain_node(target)
        elif action == "quarantine_pod":
            return self.k8s.quarantine_pod(target)
        elif action == "circuit_break":
            return self.mesh.circuit_break(target)
        elif action == "rate_limit":
            return self.mesh.rate_limit(target)
        elif action == "traffic_shed":
            return self.mesh.traffic_shed(target)
        else:
            return {"error": f"Unknown action {action}"}

    def verify_telemetry(self, component: str, score: float, threshold: float, current_ts: Optional[float] = None) -> None:
        """Verifies whether previous remediation successfully resolved the anomaly on the target."""
        event = self._active_verifications.get(component)
        if not event:
            return

        now = current_ts if current_ts is not None else time.time()

        # If score is normalized below threshold, mark as resolved!
        if score <= threshold:
            event.verification_status = "RESOLVED"
            event.resolved_at = now
            self.guard.record_action_completed(component)
            self._active_verifications.pop(component, None)
            log.info("[remediation-verified] Incident on %s RESOLVED after %s", component, event.action)
        # If verification window elapsed and score is still abnormal, escalate!
        elif now - event.timestamp > self.verification_window_s:
            event.verification_status = "ESCALATED"
            self.guard.record_action_completed(component)
            self._active_verifications.pop(component, None)
            log.warning("[remediation-escalated] Remediation on %s failed to resolve; ESCALATED", component)

    def get_state(self) -> dict[str, Any]:
        """Returns the full autonomous agent and simulation state."""
        return {
            "mode": self.mode,
            "auto_remediate": self.auto_remediate,
            "verification_window_s": self.verification_window_s,
            "active_in_flight": list(self.guard._in_flight),
            "pending_verifications": [e.to_dict() for e in self._active_verifications.values()],
            "stats": {
                "total_actions": len(self.history),
                "applied_actions": sum(1 for e in self.history if e.execution_status == "APPLIED"),
                "blocked_actions": sum(1 for e in self.history if e.execution_status == "BLOCKED_BY_SAFETY"),
                "resolved_actions": sum(1 for e in self.history if e.verification_status == "RESOLVED"),
                "escalated_actions": sum(1 for e in self.history if e.verification_status == "ESCALATED"),
            },
            "cluster": self.k8s.get_cluster_state(),
            "mesh": self.mesh.get_mesh_state(),
        }

    def get_history(self, limit: int = 50) -> list[dict[str, Any]]:
        items = list(self.history)[-max(1, min(limit, 1000)):]
        return [e.to_dict() for e in items][::-1]

    def reset(self) -> None:
        self.guard.reset()
        self.k8s.reset()
        self.mesh.reset()
        self.history.clear()
        self._active_verifications.clear()
