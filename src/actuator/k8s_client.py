"""Kubernetes actuator client with simulated cluster environment.

Enables deterministic remediation in a safe simulation environment:
- Pod restarts / rolling updates
- Deployment autoscaling (horizontal scaling)
- Node cordoning / draining
- Pod quarantining
"""
from __future__ import annotations

import time
from typing import Any, Optional

from src.common.logging_utils import get_logger

log = get_logger("k8s_client")


class SimulatedK8sClient:
    """Stateful simulation of a Kubernetes cluster for safe testing and execution."""

    def __init__(self, mode: str = "simulation"):
        self.mode = mode  # "simulation" | "dry_run" | "kubernetes"
        self._deployments: dict[str, dict[str, Any]] = {}
        self._nodes: dict[str, dict[str, Any]] = {
            "worker-node-1": {"status": "Ready", "schedulable": True, "pods": []},
            "worker-node-2": {"status": "Ready", "schedulable": True, "pods": []},
            "worker-node-3": {"status": "Ready", "schedulable": True, "pods": []},
        }
        self._init_defaults()

    def _init_defaults(self) -> None:
        default_services = [
            "dfs.DataNode$DataXceiver",
            "dfs.FSNamesystem",
            "dfs.DataNode$PacketResponder",
            "dfs.DataNode",
            "dfs.DataBlockScanner",
            "payment-service",
            "order-service",
            "api-gateway",
            "auth-service",
            "database",
        ]
        node_names = list(self._nodes.keys())
        for i, svc in enumerate(default_services):
            node = node_names[i % len(node_names)]
            self._deployments[svc] = {
                "name": svc,
                "replicas": 3,
                "available_replicas": 3,
                "status": "Running",
                "restart_count": 0,
                "assigned_node": node,
                "quarantined": False,
                "last_action": None,
                "last_action_ts": None,
            }
            self._nodes[node]["pods"].append(svc)

    def _ensure_deployment(self, component: str) -> dict[str, Any]:
        if component not in self._deployments:
            node = list(self._nodes.keys())[len(self._deployments) % len(self._nodes)]
            self._deployments[component] = {
                "name": component,
                "replicas": 3,
                "available_replicas": 3,
                "status": "Running",
                "restart_count": 0,
                "assigned_node": node,
                "quarantined": False,
                "last_action": None,
                "last_action_ts": None,
            }
            self._nodes[node]["pods"].append(component)
        return self._deployments[component]

    def restart_pod(self, component: str) -> dict[str, Any]:
        """Simulates a rolling restart of pods for the given component."""
        dep = self._ensure_deployment(component)
        dep["restart_count"] += 1
        dep["status"] = "Running"
        dep["last_action"] = "restart_pod"
        dep["last_action_ts"] = time.time()
        
        result = {
            "action": "restart_pod",
            "target": component,
            "status": "SUCCESS",
            "mode": self.mode,
            "details": {
                "deployment": component,
                "new_restart_count": dep["restart_count"],
                "replicas": dep["replicas"],
                "node": dep["assigned_node"],
                "message": f"Successfully performed rolling restart of {component} pods (generation {dep['restart_count']})",
            },
        }
        log.info("[k8s-%s] %s executed on %s", self.mode, "restart_pod", component)
        return result

    def scale_deployment(self, component: str, replicas_delta: int = 2) -> dict[str, Any]:
        """Simulates scaling up replicas to relieve traffic congestion or deadlock."""
        dep = self._ensure_deployment(component)
        old_replicas = dep["replicas"]
        dep["replicas"] = min(dep["replicas"] + replicas_delta, 10)
        dep["available_replicas"] = dep["replicas"]
        dep["last_action"] = "scale_deployment"
        dep["last_action_ts"] = time.time()

        result = {
            "action": "scale_deployment",
            "target": component,
            "status": "SUCCESS",
            "mode": self.mode,
            "details": {
                "deployment": component,
                "previous_replicas": old_replicas,
                "new_replicas": dep["replicas"],
                "message": f"Scaled deployment {component} from {old_replicas} to {dep['replicas']} replicas",
            },
        }
        log.info("[k8s-%s] %s executed on %s (%d -> %d)", self.mode, "scale_deployment", component, old_replicas, dep["replicas"])
        return result

    def drain_node(self, node: str) -> dict[str, Any]:
        """Simulates cordoning and draining a Kubernetes worker node."""
        if node not in self._nodes:
            node = list(self._nodes.keys())[0]

        target_node = self._nodes[node]
        target_node["schedulable"] = False
        target_node["status"] = "Cordoned & Drained"
        evicted_pods = list(target_node["pods"])
        target_node["pods"] = []

        # Rebalance evicted pods to other schedulable nodes
        other_nodes = [n for n, d in self._nodes.items() if n != node and d["schedulable"]]
        if other_nodes:
            for i, p in enumerate(evicted_pods):
                dest = other_nodes[i % len(other_nodes)]
                self._nodes[dest]["pods"].append(p)
                if p in self._deployments:
                    self._deployments[p]["assigned_node"] = dest

        result = {
            "action": "drain_node",
            "target": node,
            "status": "SUCCESS",
            "mode": self.mode,
            "details": {
                "node": node,
                "evicted_pods": evicted_pods,
                "message": f"Cordoned node {node} and evicted {len(evicted_pods)} pods to healthy nodes",
            },
        }
        log.info("[k8s-%s] %s executed on node %s", self.mode, "drain_node", node)
        return result

    def quarantine_pod(self, component: str) -> dict[str, Any]:
        """Isolates failing pod from service mesh / ingress routing."""
        dep = self._ensure_deployment(component)
        dep["quarantined"] = True
        dep["last_action"] = "quarantine_pod"
        dep["last_action_ts"] = time.time()

        result = {
            "action": "quarantine_pod",
            "target": component,
            "status": "SUCCESS",
            "mode": self.mode,
            "details": {
                "deployment": component,
                "message": f"Isolated {component} endpoints from active traffic routing",
            },
        }
        log.info("[k8s-%s] %s executed on %s", self.mode, "quarantine_pod", component)
        return result

    def get_cluster_state(self) -> dict[str, Any]:
        """Returns the current state of simulated deployments and nodes."""
        return {
            "mode": self.mode,
            "deployments": {k: dict(v) for k, v in self._deployments.items()},
            "nodes": {k: dict(v) for k, v in self._nodes.items()},
        }

    def reset(self) -> None:
        """Resets cluster back to default state."""
        self._deployments.clear()
        for d in self._nodes.values():
            d["schedulable"] = True
            d["status"] = "Ready"
            d["pods"] = []
        self._init_defaults()
