# Log Cascade Agent

## Unsupervised Log Anomaly & Failure Cascade Predictor

An autonomous SRE/operations agent that monitors microservice logs, detects abnormal system behavior without manually defined anomaly patterns, identifies potential failure cascades and root-cause services, and can safely trigger deterministic remediation actions.

---

## 1. Problem Statement

Modern microservice applications can generate millions of log lines.

When a service starts degrading, the failure may propagate through dependent services:

```text
Database
   ↓
Payment Service
   ↓
Order Service
   ↓
API Gateway
   ↓
Frontend
```

The goal of this project is to detect abnormal behavior early, determine whether anomalies are forming an incident, identify the likely origin of the failure cascade, estimate the blast radius, and safely perform remediation.

The system does not depend on manually writing rules such as:

```text
if "database timeout" in log:
    alert()
```

Instead, it learns normal log-event sequences and detects deviations from that learned behavior.

---

# 2. Project Objectives

The system aims to:

* Collect logs from microservices.
* Normalize and preprocess raw log messages.
* Convert raw logs into structured templates using Drain3.
* Assign stable Event IDs to log templates.
* Create sliding sequences of Event IDs.
* Train an unsupervised LSTM Autoencoder using normal system behavior.
* Calculate reconstruction error for incoming sequences.
* Dynamically determine anomaly thresholds using EVT/POT.
* Detect abnormal services.
* Correlate anomalies across services.
* Detect failure propagation.
* Identify a likely root-cause service.
* Calculate incident blast radius.
* Determine incident severity.
* Apply deterministic safety policies.
* Trigger controlled Kubernetes or service-mesh remediation.
* Verify whether remediation resolved the incident.

---

# 3. High-Level Architecture

```text
                         LOG SOURCES
                              │
             ┌────────────────┴────────────────┐
             │                                 │
       Microservice Logs                  LogHub Dataset
             │                                 │
             ▼                                 │
    Fluent Bit / Vector /                     │
        Logstash                              │
             │                                 │
             └──────────────┬──────────────────┘
                            ▼
                   ┌─────────────────┐
                   │    FastAPI      │
                   │ Log Ingestion   │
                   └────────┬────────┘
                            │
                            ▼
                   ┌─────────────────┐
                   │  Preprocessor   │
                   │ Normalize Logs  │
                   └────────┬────────┘
                            │
                            ▼
                   ┌─────────────────┐
                   │     Drain3      │
                   │ Template Mining │
                   └────────┬────────┘
                            │
                       Event IDs
                            │
                            ▼
                   ┌─────────────────┐
                   │ Sliding Window  │
                   │ Event Sequences │
                   └────────┬────────┘
                            │
                            ▼
                   ┌─────────────────┐
                   │ LSTM Autoencoder│
                   │     PyTorch     │
                   └────────┬────────┘
                            │
                    Reconstruction
                       Error
                            │
                            ▼
                   ┌─────────────────┐
                   │   EVT / POT     │
                   │ Dynamic Threshold│
                   └────────┬────────┘
                            │
                         Anomaly
                            │
                            ▼
                   ┌─────────────────┐
                   │    Incident     │
                   │    Correlator   │
                   └────────┬────────┘
                            │
                            ▼
                   ┌─────────────────┐
                   │ Cascade Analyzer│
                   │   + Root Cause  │
                   └────────┬────────┘
                            │
                            ▼
                 ┌───────────────────────┐
                 │ Incident Context      │
                 │                       │
                 │ Root Cause            │
                 │ Blast Radius          │
                 │ Severity              │
                 │ Propagation Path      │
                 └───────────┬───────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │  Safety Guard   │
                    └────────┬────────┘
                             │
                 ┌───────────┴───────────┐
                 ▼                       ▼
          Kubernetes API          Envoy / Istio
          Remediation             Traffic Control
                 │                       │
                 └───────────┬───────────┘
                             ▼
                       Verification
                             │
                  ┌──────────┴──────────┐
                  ▼                     ▼
               RESOLVED              ESCALATED
```

---

# 4. How Data Enters the System

The system does not need to directly modify the microservice application.

A microservice produces normal application/container logs.

Example:

```text
2026-10-08 11:10:01 INFO Payment request received order=1234
2026-10-08 11:10:02 INFO Connecting to database
2026-10-08 11:10:03 ERROR Database connection timeout
```

A log collector such as:

* Fluent Bit
* Vector
* Logstash

can collect these logs and forward them to the system.

The system exposes:

```text
POST /v1/logs/ingest
```

Example request:

```json
{
  "timestamp": "2026-10-08T11:10:03Z",
  "service_id": "payment-service",
  "pod_id": "payment-123",
  "level": "ERROR",
  "message": "Database connection timeout"
}
```

The uploaded architecture defines this API as the ingestion boundary for log forwarders.

---

# 5. What Anomaly Are We Detecting?

The system does not simply detect the presence of an `ERROR` log.

It detects abnormal changes in the **sequence and behavior of log events** compared with learned normal behavior.

### Normal example

```text
Request received
        ↓
Authentication
        ↓
Database query
        ↓
Payment processed
        ↓
Response sent
```

Represented as:

```text
E1 → E4 → E7 → E9 → E12
```

### Abnormal example

```text
Request received
        ↓
Database timeout
        ↓
Retry
        ↓
Database timeout
        ↓
Retry
        ↓
Database timeout
```

Represented as:

```text
E1 → E17 → E22 → E17 → E22 → E17
```

The LSTM Autoencoder should produce a higher reconstruction error for behavior that differs significantly from normal sequences.

---

# 6. Incident vs Anomaly

An individual anomaly does not automatically mean an incident.

Example:

```text
10:00:01 payment-service anomaly
```

could be a temporary abnormal event.

An incident is formed when multiple related anomalies occur within a time window and show meaningful service-level correlation.

Example:

```text
10:00:01 payment anomaly
10:00:05 payment anomaly
10:00:08 order anomaly
10:00:12 gateway anomaly
```

If the dependency graph is:

```text
payment
   ↓
order
   ↓
gateway
```

the system can identify a potential cascade:

```text
payment → order → gateway
```

---

# 7. Incident Summary

For each incident, the system should generate structured operational information.

Example:

```json
{
  "incident_id": "INC-001",
  "started_at": "2026-10-08T11:10:03Z",
  "root_cause_candidate": "payment-service",
  "affected_services": [
    "payment-service",
    "order-service",
    "gateway"
  ],
  "cascade": [
    "payment-service",
    "order-service",
    "gateway"
  ],
  "blast_radius": 0.375,
  "severity": "HIGH",
  "status": "INVESTIGATING"
}
```

The system summarizes structured operational information rather than sending all raw logs to an LLM.

---

# 8. Detection Pipeline

## Step 1 — Raw Log

```text
ERROR Database connection timeout from 10.20.4.25
```

## Step 2 — Preprocessing

```text
ERROR Database connection timeout from <IP>
```

## Step 3 — Drain3

```text
Template:
ERROR Database connection timeout from <IP>

Event ID:
E17
```

## Step 4 — Event Sequence

```text
E1 E4 E7 E17 E17 E17 E22
```

## Step 5 — Sliding Window

Example:

```text
Window = 30
Stride = 5
```

Sequence:

```text
[E1,E4,E7,E17,...]
```

## Step 6 — Autoencoder

```text
Event sequence
      ↓
Embedding
      ↓
LSTM Encoder
      ↓
Latent representation
      ↓
LSTM Decoder
      ↓
Reconstructed sequence
```

## Step 7 — Reconstruction Error

```text
score = reconstruction error
```

## Step 8 — EVT/POT

The system compares the score against a dynamically estimated extreme-value threshold.

## Step 9 — Anomaly

```text
score = 8.4
threshold = 3.2

ANOMALY = TRUE
```

---

# 9. Failure Cascade Detection

The system maintains a service dependency graph.

Example:

```text
Frontend
    ↓
Gateway
    ↓
Order Service
    ↓
Payment Service
    ↓
Database
```

Suppose anomalies occur:

```text
11:10:01 Database
11:10:05 Payment
11:10:08 Order
11:10:12 Gateway
```

The system evaluates:

* temporal ordering
* time lag
* service dependencies
* anomaly strength
* affected services

Potential result:

```text
Database
    ↓
Payment
    ↓
Order
    ↓
Gateway
```

The first service in a supported propagation path becomes a root-cause candidate.

This is a candidate, not an absolute guarantee of causality.

---

# 10. Blast Radius

The system determines how many services are affected.

Example:

```text
Total services = 8

Affected:
database
payment
order

Affected = 3
```

Therefore:

```text
Blast Radius = 3 / 8
             = 37.5%
```

---

# 11. Severity

Severity can consider:

```text
Anomaly score
+
Number of affected services
+
Blast radius
+
Propagation
+
Service criticality
```

Possible levels:

```text
LOW
MEDIUM
HIGH
CRITICAL
```

---

# 12. Autonomous Control Plane

The agent should not allow the ML model to directly execute remediation.

Instead:

```text
Anomaly
   ↓
Incident
   ↓
Root Cause
   ↓
Decision Engine
   ↓
Safety Guard
   ↓
Approved Action
```

Safety checks can include:

* cooldown period
* maximum simultaneous restarts
* service allowlist
* retry limits
* active remediation check
* maximum blast radius
* action type restrictions

Example:

```text
Never restart more than 20%
of service replicas at once.
```

---

# 13. Remediation

Potential remediation actions include:

### Kubernetes

```text
Restart pod
Scale deployment
Check pod health
```

### Envoy/Istio

```text
Circuit breaking
Traffic shedding
Traffic shifting
Rate limiting
```

Remediation will initially be tested in a controlled local Kubernetes environment rather than a production cluster.

---

# 14. Verification

After remediation:

```text
Action
  ↓
Wait
  ↓
Collect new logs
  ↓
Calculate anomaly score
  ↓
Check service health
  ↓
Compare with previous state
```

If the anomaly disappears:

```text
RESOLVED
```

If it continues:

```text
RETRY / ESCALATE
```

---

# 15. Project Structure

```text
log-cascade-agent/
│
├── configs/
│   ├── drain3_config.ini
│   ├── model_config.yaml
│   └── agent_rules.yaml
│
├── data/
│   ├── raw/
│   ├── processed/
│   └── checkpoints/
│       ├── drain3_state.bin
│       └── autoencoder_nominal.pt
│
├── src/
│   ├── __init__.py
│   │
│   ├── common/
│   │   ├── config.py
│   │   └── logger.py
│   │
│   ├── parser/
│   │   ├── __init__.py
│   │   ├── preprocessor.py
│   │   └── template_miner.py
│   │
│   ├── buffer/
│   │   ├── __init__.py
│   │   └── sliding_window.py
│   │
│   ├── model/
│   │   ├── __init__.py
│   │   ├── autoencoder.py
│   │   ├── threshold.py
│   │   └── trainer.py
│   │
│   ├── cascade/
│   │   ├── __init__.py
│   │   ├── causality_graph.py
│   │   └── root_cause_finder.py
│   │
│   ├── actuator/
│   │   ├── __init__.py
│   │   ├── safety_guard.py
│   │   ├── k8s_client.py
│   │   └── service_mesh_client.py
│   │
│   └── engine/
│       ├── __init__.py
│       └── runner.py
│
├── api/
│   ├── __init__.py
│   ├── app.py
│   ├── routes.py
│   └── schemas.py
│
├── tests/
│   ├── test_preprocessor.py
│   ├── test_drain.py
│   ├── test_window.py
│   ├── test_autoencoder.py
│   ├── test_threshold.py
│   ├── test_cascade.py
│   ├── test_root_cause.py
│   ├── test_safety_guard.py
│   └── test_api.py
│
├── .gitignore
├── Dockerfile
├── requirements.txt
└── README.md
```

---

# 16. Team Responsibilities

## Member 1 — Data & ML

### Owns

```text
src/parser/
src/buffer/
src/model/
```

### Responsibilities

* Log preprocessing
* Drain3
* Template mining
* Event IDs
* Sliding windows
* PyTorch model
* LSTM Autoencoder
* Training
* Reconstruction error
* EVT/POT
* Anomaly events

### Deliverable

```text
Raw logs
   ↓
Event IDs
   ↓
Sequences
   ↓
Anomaly events
```

---

# Member 2 — Cascade & Root Cause

### Owns

```text
src/cascade/
```

### Responsibilities

* Service topology
* Dependency graph
* Temporal correlation
* Incident grouping
* Failure cascade detection
* Root-cause candidate
* Blast radius
* Severity
* Incident context

### Deliverable

```text
Anomaly events
   ↓
Incident
   ↓
Root cause
   ↓
Cascade
   ↓
Blast radius
   ↓
Severity
```

---

# Member 3 — Agent & Infrastructure

### Owns

```text
api/
src/actuator/
src/engine/
```

### Responsibilities

* FastAPI
* Log ingestion
* API endpoints
* Agent orchestration
* Decision engine
* Safety guard
* Kubernetes integration
* Envoy/Istio integration
* Remediation
* Verification
* Dashboard integration

### Deliverable

```text
Incident
   ↓
Decision
   ↓
Safety
   ↓
Remediation
   ↓
Verification
```

---

# 17. Interfaces Between Team Members

The team should agree on data contracts before implementation.

## Member 1 → Member 2

```json
{
  "timestamp": "2026-10-08T10:00:01Z",
  "service_id": "payment",
  "pod_id": "payment-01",
  "anomaly_score": 8.7,
  "threshold": 3.2,
  "is_anomaly": true
}
```

---

## Member 2 → Member 3

```json
{
  "incident_id": "INC-001",
  "root_cause": "payment",
  "affected_services": [
    "payment",
    "order",
    "gateway"
  ],
  "cascade": [
    "payment",
    "order",
    "gateway"
  ],
  "blast_radius": 0.375,
  "severity": "HIGH"
}
```

This separation allows each team member to develop and test their component independently.

---

# 18. Data Sources

For development and model training, the project uses Loghub datasets including:

```text
HDFS
BGL
Thunderbird
ZooKeeper
```

These datasets provide historical logs for developing and evaluating the anomaly-detection pipeline.

For a real deployment, the input will instead come from application/container logs through a log collection pipeline.

---

# 19. Technology Stack

## Programming

```text
Python
```

## Log Processing

```text
Drain3
```

## Machine Learning

```text
PyTorch
scikit-learn
SciPy
```

## Anomaly Detection

```text
LSTM Autoencoder
Reconstruction Error
EVT / POT
Isolation Forest baseline
```

## Backend

```text
FastAPI
Pydantic
Uvicorn
```

## Data

```text
PostgreSQL
```

## Graph / Cascade

```text
NetworkX
```

## Infrastructure

```text
Docker
Kubernetes
Envoy / Istio
```

## Testing

```text
Pytest
```

---

# 20. Development Plan

### Phase 1 — Project Setup

```text
Git
Virtual Environment
Project Structure
Dependencies
```

### Phase 2 — Log Pipeline

```text
LogHub
↓
Preprocessor
↓
Drain3
↓
Event IDs
```

### Phase 3 — Sequence Processing

```text
Event IDs
↓
Sliding Windows
↓
Tensor Representation
```

### Phase 4 — ML

```text
Normal Logs
↓
LSTM Autoencoder
↓
Training
↓
Reconstruction Error
```

### Phase 5 — Dynamic Detection

```text
Reconstruction Errors
↓
EVT/POT
↓
Anomaly Events
```

### Phase 6 — Incident Intelligence

```text
Anomalies
↓
Correlation
↓
Dependency Graph
↓
Cascade
↓
Root Cause
```

### Phase 7 — Agent

```text
Incident
↓
Decision
↓
Safety Guard
↓
Remediation
↓
Verification
```

### Phase 8 — APIs & Dashboard

```text
FastAPI
↓
React Dashboard
```

### Phase 9 — Integration

```text
Complete End-to-End Test
```

---

# 21. Example End-to-End Scenario

Suppose the system has:

```text
Frontend
   ↓
Gateway
   ↓
Order
   ↓
Payment
   ↓
Database
```

The database starts experiencing connection problems.

Logs change from:

```text
Database query successful
Database query successful
Database query successful
```

to:

```text
Database connection timeout
Retrying database
Database connection timeout
Retrying database
Database connection timeout
```

Drain3 converts these into event IDs.

The sliding-window representation changes.

The autoencoder produces:

```text
Reconstruction Error = 8.7
Dynamic Threshold = 3.2
```

Therefore:

```text
ANOMALY
```

Then:

```text
Database anomaly
       ↓
Payment anomaly
       ↓
Order anomaly
       ↓
Gateway anomaly
```

The cascade engine identifies:

```text
Root Cause Candidate:
Database
```

and:

```text
Blast Radius:
4 services
```

The incident becomes:

```text
INC-001
Severity: CRITICAL
Status: INVESTIGATING
```

The decision engine evaluates remediation.

The safety guard checks whether remediation is permitted.

If approved:

```text
Kubernetes
    ↓
Controlled remediation
```

Then the agent observes the system again.

If anomaly scores return to normal:

```text
INC-001
Status: RESOLVED
```

---

# 22. Important Design Principle

The system is **not an LLM chatbot**.

The core detection and decision pipeline is deterministic/ML-based:

```text
Drain3
+
LSTM Autoencoder
+
Reconstruction Error
+
EVT/POT
+
Temporal Correlation
+
Dependency Graph
+
State Machine
+
Safety Rules
+
Kubernetes APIs
```

The goal is to create an engineering system that can operate on real log streams and perform operational analysis/actions rather than simply explaining logs in natural language.

---

# 23. Current Development Status

The project should be developed incrementally.

### Current starting point

```text
[ ] Project repository
[ ] Virtual environment
[ ] .gitignore
[ ] Dependencies
[ ] LogHub data
```

### Next

```text
[ ] Preprocessor
[ ] Drain3
[ ] Event ID generation
[ ] Drain3 persistence
```

### Then

```text
[ ] Sliding windows
[ ] LSTM Autoencoder
[ ] Training
[ ] Reconstruction scoring
[ ] EVT/POT
```

### Then

```text
[ ] Incident correlation
[ ] Service topology
[ ] Cascade detection
[ ] Root-cause analysis
```

### Finally

```text
[ ] FastAPI
[ ] Safety guard
[ ] Kubernetes
[ ] Remediation
[ ] Verification
[ ] Dashboard
[ ] End-to-end testing
```

---

# 24. Success Criteria

The project is considered successful when it can demonstrate:

```text
Raw microservice logs
        ↓
Automatic template extraction
        ↓
Event sequence generation
        ↓
Normal behavior modeling
        ↓
Real-time anomaly detection
        ↓
Cross-service incident detection
        ↓
Failure cascade identification
        ↓
Root-cause candidate
        ↓
Blast-radius calculation
        ↓
Safe remediation
        ↓
Post-remediation verification
```

The final demonstration should preferably use a controlled Kubernetes environment where a failure is deliberately introduced and the agent detects, analyzes, mitigates, and verifies recovery.
