"""Service mesh actuator client with simulated Envoy / Istio policies.

Enables safe simulation of traffic-level mitigations:
- Circuit breaking
- Rate limiting
- Traffic shedding / shifting
"""
from __future__ import annotations

import time
from typing import Any

from src.common.logging_utils import get_logger

log = get_logger("service_mesh")


class SimulatedServiceMeshClient:
    """Stateful simulation of Envoy / Istio service mesh traffic policies."""

    def __init__(self, mode: str = "simulation"):
        self.mode = mode
        self._circuit_breakers: dict[str, dict[str, Any]] = {}
        self._rate_limits: dict[str, dict[str, Any]] = {}
        self._traffic_shed: dict[str, dict[str, Any]] = {}

    def circuit_break(self, component: str, consecutive_errors: int = 3, interval_s: int = 10) -> dict[str, Any]:
        """Enables circuit breaking on the service to prevent cascade propagation."""
        policy = {
            "component": component,
            "consecutive_errors": consecutive_errors,
            "interval_s": interval_s,
            "active": True,
            "applied_at": time.time(),
        }
        self._circuit_breakers[component] = policy
        log.info("[mesh-%s] Applied circuit breaker on %s", self.mode, component)
        return {
            "action": "circuit_break",
            "target": component,
            "status": "SUCCESS",
            "mode": self.mode,
            "details": {
                "message": f"Circuit breaker activated for {component} after {consecutive_errors} consecutive failures",
                "policy": policy,
            },
        }

    def rate_limit(self, component: str, rps: int = 50) -> dict[str, Any]:
        """Applies rate limiting to prevent downstream overload."""
        policy = {
            "component": component,
            "rps": rps,
            "active": True,
            "applied_at": time.time(),
        }
        self._rate_limits[component] = policy
        log.info("[mesh-%s] Applied rate limit on %s (%d rps)", self.mode, component, rps)
        return {
            "action": "rate_limit",
            "target": component,
            "status": "SUCCESS",
            "mode": self.mode,
            "details": {
                "message": f"Rate limit applied on {component} capped at {rps} req/sec",
                "policy": policy,
            },
        }

    def traffic_shed(self, component: str, drop_percentage: int = 25) -> dict[str, Any]:
        """Sheds non-critical traffic for an overloaded component."""
        policy = {
            "component": component,
            "drop_percentage": drop_percentage,
            "active": True,
            "applied_at": time.time(),
        }
        self._traffic_shed[component] = policy
        log.info("[mesh-%s] Applied traffic shedding on %s (%d%%)", self.mode, component, drop_percentage)
        return {
            "action": "traffic_shed",
            "target": component,
            "status": "SUCCESS",
            "mode": self.mode,
            "details": {
                "message": f"Shedding {drop_percentage}% of non-essential traffic for {component}",
                "policy": policy,
            },
        }

    def get_mesh_state(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "circuit_breakers": {k: dict(v) for k, v in self._circuit_breakers.items()},
            "rate_limits": {k: dict(v) for k, v in self._rate_limits.items()},
            "traffic_shed": {k: dict(v) for k, v in self._traffic_shed.items()},
        }

    def reset(self) -> None:
        self._circuit_breakers.clear()
        self._rate_limits.clear()
        self._traffic_shed.clear()
