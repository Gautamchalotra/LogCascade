"""Safety guard for autonomous remediation.

Enforces deterministic safety constraints before any remediation action
(restart pod, scale deployment, circuit break, etc.) can be executed.
Supports cooldown, max simultaneous actions, allowlists, and blast-radius caps.
"""
from __future__ import annotations

import fnmatch
import time
from dataclasses import asdict, dataclass, field
from typing import Optional

from src.common.logging_utils import get_logger

log = get_logger("safety_guard")


@dataclass
class SafetyDecision:
    approved: bool
    reason: str
    action: str
    component: str
    blast_radius: float = 0.0
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)


class SafetyGuard:
    def __init__(
        self,
        cooldown_s: float = 60.0,
        max_simultaneous: int = 2,
        max_blast_radius: float = 0.75,
        allowlist: Optional[list[str]] = None,
        allowed_actions: Optional[list[str]] = None,
    ):
        self.cooldown_s = cooldown_s
        self.max_simultaneous = max_simultaneous
        self.max_blast_radius = max_blast_radius
        self.allowlist = allowlist or ["*"]
        self.allowed_actions = allowed_actions or [
            "restart_pod",
            "scale_deployment",
            "circuit_break",
            "rate_limit",
            "drain_node",
        ]
        self._last_action_ts: dict[str, float] = {}
        self._in_flight: set[str] = set()

    def is_allowlisted(self, component: str) -> bool:
        if "*" in self.allowlist:
            return True
        return any(fnmatch.fnmatch(component, pat) for pat in self.allowlist)

    def evaluate(
        self,
        action: str,
        component: str,
        blast_radius: float = 0.0,
        current_ts: Optional[float] = None,
    ) -> SafetyDecision:
        now = current_ts if current_ts is not None else time.time()

        # 1. Action type check
        if action not in self.allowed_actions:
            return SafetyDecision(
                approved=False,
                reason=f"Action '{action}' is not in allowed_actions {self.allowed_actions}",
                action=action,
                component=component,
                blast_radius=blast_radius,
                timestamp=now,
            )

        # 2. Component allowlist check
        if not self.is_allowlisted(component):
            return SafetyDecision(
                approved=False,
                reason=f"Component '{component}' is not in allowlist {self.allowlist}",
                action=action,
                component=component,
                blast_radius=blast_radius,
                timestamp=now,
            )

        # 3. Blast radius check
        if blast_radius > self.max_blast_radius:
            return SafetyDecision(
                approved=False,
                reason=(
                    f"Blast radius {blast_radius:.2f} exceeds safety threshold "
                    f"{self.max_blast_radius:.2f}; requiring human intervention"
                ),
                action=action,
                component=component,
                blast_radius=blast_radius,
                timestamp=now,
            )

        # 4. Simultaneous in-flight actions check
        if len(self._in_flight) >= self.max_simultaneous and component not in self._in_flight:
            return SafetyDecision(
                approved=False,
                reason=(
                    f"Max simultaneous actions ({self.max_simultaneous}) reached "
                    f"with active {list(self._in_flight)}"
                ),
                action=action,
                component=component,
                blast_radius=blast_radius,
                timestamp=now,
            )

        # 5. Cooldown check
        last_ts = self._last_action_ts.get(component, -1e18)
        if now - last_ts < self.cooldown_s:
            remaining = self.cooldown_s - (now - last_ts)
            return SafetyDecision(
                approved=False,
                reason=f"Component '{component}' is in cooldown for {remaining:.1f}s more",
                action=action,
                component=component,
                blast_radius=blast_radius,
                timestamp=now,
            )

        # Approved
        return SafetyDecision(
            approved=True,
            reason="Safety policies passed",
            action=action,
            component=component,
            blast_radius=blast_radius,
            timestamp=now,
        )

    def record_action_started(self, component: str, current_ts: Optional[float] = None) -> None:
        now = current_ts if current_ts is not None else time.time()
        self._last_action_ts[component] = now
        self._in_flight.add(component)

    def record_action_completed(self, component: str) -> None:
        self._in_flight.discard(component)

    def reset(self) -> None:
        self._last_action_ts.clear()
        self._in_flight.clear()
