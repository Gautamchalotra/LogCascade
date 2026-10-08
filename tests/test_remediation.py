import pytest
from src.actuator.k8s_client import SimulatedK8sClient
from src.actuator.remediation_agent import RemediationAgent, RemediationEvent
from src.actuator.safety_guard import SafetyGuard
from src.actuator.service_mesh_client import SimulatedServiceMeshClient
from src.common.schemas import Alert


def test_safety_guard_policies():
    guard = SafetyGuard(
        cooldown_s=30.0,
        max_simultaneous=2,
        max_blast_radius=0.5,
        allowlist=["dfs.*", "payment-*"],
        allowed_actions=["restart_pod", "scale_deployment", "circuit_break"],
    )

    # 1. Allowed action & allowlisted component
    dec = guard.evaluate("restart_pod", "dfs.DataNode", blast_radius=0.1, current_ts=100.0)
    assert dec.approved is True

    # 2. Unknown action rejected
    dec = guard.evaluate("delete_cluster", "dfs.DataNode", blast_radius=0.1, current_ts=100.0)
    assert dec.approved is False
    assert "not in allowed_actions" in dec.reason

    # 3. Non-allowlisted component rejected
    dec = guard.evaluate("restart_pod", "untrusted_service", blast_radius=0.1, current_ts=100.0)
    assert dec.approved is False
    assert "not in allowlist" in dec.reason

    # 4. Blast radius exceeding threshold rejected
    dec = guard.evaluate("restart_pod", "dfs.DataNode", blast_radius=0.8, current_ts=100.0)
    assert dec.approved is False
    assert "exceeds safety threshold" in dec.reason

    # 5. Cooldown enforcement
    guard.record_action_started("dfs.DataNode", current_ts=100.0)
    dec = guard.evaluate("restart_pod", "dfs.DataNode", blast_radius=0.1, current_ts=115.0)
    assert dec.approved is False
    assert "cooldown" in dec.reason

    # After cooldown expires
    dec = guard.evaluate("restart_pod", "dfs.DataNode", blast_radius=0.1, current_ts=135.0)
    assert dec.approved is True


def test_safety_guard_max_simultaneous():
    guard = SafetyGuard(max_simultaneous=2)
    guard.record_action_started("svc1", current_ts=10.0)
    guard.record_action_started("svc2", current_ts=10.0)

    # 3rd simultaneous action rejected
    dec = guard.evaluate("restart_pod", "svc3", current_ts=15.0)
    assert dec.approved is False
    assert "Max simultaneous actions" in dec.reason

    # Complete one action
    guard.record_action_completed("svc1")
    dec = guard.evaluate("restart_pod", "svc3", current_ts=15.0)
    assert dec.approved is True


def test_simulated_k8s_client():
    k8s = SimulatedK8sClient(mode="simulation")

    # Restart
    res = k8s.restart_pod("payment-service")
    assert res["status"] == "SUCCESS"
    assert res["details"]["new_restart_count"] == 1

    # Scale
    res = k8s.scale_deployment("payment-service", replicas_delta=3)
    assert res["status"] == "SUCCESS"
    assert res["details"]["new_replicas"] == 6

    # Drain node
    res = k8s.drain_node("worker-node-1")
    assert res["status"] == "SUCCESS"
    state = k8s.get_cluster_state()
    assert state["nodes"]["worker-node-1"]["schedulable"] is False

    # Quarantine
    res = k8s.quarantine_pod("order-service")
    assert res["status"] == "SUCCESS"
    state = k8s.get_cluster_state()
    assert state["deployments"]["order-service"]["quarantined"] is True


def test_simulated_service_mesh_client():
    mesh = SimulatedServiceMeshClient(mode="simulation")

    # Circuit break
    res = mesh.circuit_break("database", consecutive_errors=5)
    assert res["status"] == "SUCCESS"

    # Rate limit
    res = mesh.rate_limit("api-gateway", rps=100)
    assert res["status"] == "SUCCESS"

    state = mesh.get_mesh_state()
    assert "database" in state["circuit_breakers"]
    assert "api-gateway" in state["rate_limits"]


def test_remediation_agent_autonomous_decision_and_verification():
    agent = RemediationAgent(mode="simulation", auto_remediate=True, verification_window_s=30.0)

    # 1. Critical alert triggers autonomous pod restart
    alert = Alert(
        ts=100.0,
        component="dfs.DataNode$DataXceiver",
        key="blk_1",
        score=5.0,
        threshold=1.5,
        severity="critical",
        cascade_level="none",
    )
    event = agent.process_alert(alert, cascade_state={"level": "none", "active": ["dfs.DataNode$DataXceiver"]})
    assert event is not None
    assert event.action == "restart_pod"
    assert event.safety_approved is True
    assert event.execution_status == "APPLIED"
    assert event.verification_status == "PENDING_VERIFICATION"

    # 2. Telemetry verification: anomaly disappears -> RESOLVED
    agent.verify_telemetry("dfs.DataNode$DataXceiver", score=1.0, threshold=1.5, current_ts=110.0)
    assert event.verification_status == "RESOLVED"
    assert event.resolved_at == 110.0

    # 3. Imminent cascade triggers circuit break or root-cause remediation
    cascade_alert = Alert(
        ts=200.0,
        component="dfs.FSNamesystem",
        key="blk_2",
        score=4.0,
        threshold=1.5,
        severity="critical",
        cascade_level="imminent",
        at_risk=[("dfs.DataNode", 0.8), ("dfs.DataNode$PacketResponder", 0.7)],
    )
    c_event = agent.process_alert(cascade_alert, cascade_state={"level": "imminent", "active": ["dfs.FSNamesystem", "dfs.DataNode"]})
    assert c_event is not None
    assert c_event.action == "circuit_break"
    assert c_event.execution_status == "APPLIED"

    # 4. Manual trigger
    man_event = agent.trigger_manual_action("scale_deployment", "dfs.DataNode$PacketResponder")
    assert man_event.action == "scale_deployment"
    assert man_event.execution_status == "APPLIED"
