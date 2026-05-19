# Incident Lifecycle & Reverse Routing

> Sub-doc of `../DESIGN.md`. Owns: 7-state machine, RecoveryEvaluator, reverse routing under the shared Canary subprocess.

## Why this exists

Route-out is **mitigation**, not closure. The original Otter sketch ended at `Notification → PostMortem → END`, which left two production gaps:

1. No "return-to-normal" path — fallback stays forever, accruing cost / risk.
2. No deterministic gate between "mitigated" and "resolved" — `temporary_route_ttl_minutes` was treated as a switchback trigger, which is dangerous when the original model has not recovered.

## State machine

```
DETECTED
  → INVESTIGATING
  → MITIGATING
  → MITIGATED_ON_FALLBACK
  → RECOVERY_MONITORING
  → RESTORE_CANARY
  → RESOLVED
```

Alternative tails:

```
MITIGATED_ON_FALLBACK
  → RECOVERY_BLOCKED
  → HUMAN_REVIEW
```

| State | Owner | Exit condition |
|-------|-------|----------------|
| DETECTED | Trigger Intake | Normalized IncidentTrigger emitted |
| INVESTIGATING | Evidence + Eval+Drift blocks | EvidenceBundle + EvalDriftReport |
| MITIGATING | Decision + PolicyGate + Canary | ApprovedRoutingDecision + canary guard window passes |
| MITIGATED_ON_FALLBACK | RecoveryMonitor | Stable on fallback, observation window passes |
| RECOVERY_MONITORING | RecoveryEvaluator | RecoveryCandidate proposed |
| RESTORE_CANARY | Canary subprocess (`route_direction=restore`) | Restore canary guard window passes |
| RESOLVED | Closure | Postmortem drafted |
| RECOVERY_BLOCKED | RecoveryEvaluator | Original model still unhealthy beyond TTL, fallback cost rising |
| HUMAN_REVIEW | Action Center | Operator decides stay / restore / escalate |

## RecoveryEvaluator agent

Separate from RoutingDecisionAgent — **agents must not approve their own decisions**. Outbound routing and return-to-normal share infrastructure but split authority:

| Concern | Outbound | Restore |
|---------|----------|---------|
| Candidate generation | RoutingDecisionAgent | RecoveryEvaluator |
| Authorization | PolicyGate | PolicyGate (same DMN, restore-direction rules) |
| Execution | Canary subprocess (`route_direction=outbound`) | Canary subprocess (`route_direction=restore`) |

Shared library: model ranking, cost estimation, kill-switch threshold logic.

## Five conditions for restore

All five must hold before RecoveryEvaluator emits a restore candidate:

1. **Original model runtime healthy** — error_rate, latency_p95, availability sit at or under baseline for N consecutive clean windows.
2. **Original model quality recovered** — shadow eval or probe eval no longer below baseline; drift test not significant.
3. **Model identity acceptable** — no un-approved silent upgrade detected on the original model (see `model-identity.md`). Identity shift → restore must go through HUMAN_REVIEW.
4. **Restore mechanism safe** — feature flag / config flip / traffic split exists; rollback target verified.
5. **Continuing on fallback carries cost or risk** — fallback is more expensive, slower, or carries data-policy risk, OR temporary route TTL is approaching.

`temporary_route_ttl_minutes` expiry triggers **re-evaluation**, never auto-switchback.

## Severity → recovery windows

| Severity | Original model health window | Shadow eval requirement | Restore method |
|----------|------------------------------|--------------------------|----------------|
| LOW | 2 × 10 min clean | shallow probe pass | auto restore canary |
| MEDIUM | 2 × 15 min clean | shallow + sampled eval pass | auto restore canary |
| HIGH | 3 × 30 min clean | deep eval pass | human optional; default canary |
| CRITICAL | 24 hr observation OR human review | deep eval + human review | human-approved restore |

Demo: use compressed timer (e.g. 30–90s) but display policy-configured window in the UI.

## BPMN placement

Restore runs through the **same** Canary subprocess (Block 5) — never write a second canary engine. The `RouteDirection` enum drives:

- Which kill-switch threshold table applies (restore can be stricter than outbound on safety).
- Whether `rollback_target` is the fallback (for restore canary) or the original (for outbound canary).
- Whether monitoring snapshots compare against fallback baseline or original baseline.

## Schema

### Trigger intake (Block 1)

```python
from typing import Literal
from pydantic import BaseModel


class RawTriggerEnvelope(BaseModel):
    source: Literal["vendor_webhook", "metrics_webhook", "timer"]
    received_at: str
    payload_ref: str | None
    payload: dict


class IncidentTrigger(BaseModel):
    incident_id: str
    correlation_key: str
    trigger_type: Literal["reactive", "proactive"]
    tenant_id: str
    env: Literal["dev", "staging", "prod"]
    vendor: str | None
    model: str | None
    incident_type: str
    observed_at: str
    payload_ref: str | None


class EvidencePlan(BaseModel):
    incident_id: str
    required_agents: list[str]
    optional_agents: list[str]
    timeout_budget_seconds: int
    evidence_window_start: str
    evidence_window_end: str


class EvalRequest(BaseModel):
    incident_id: str
    run_eval: bool
    eval_depth: Literal["none", "shallow", "deep"]
    sample_selector: dict
    baseline_ref: str
    rubric_version: str
```

### Recovery direction

```python
from enum import Enum
from pydantic import Field


class RouteDirection(str, Enum):
    OUTBOUND = "outbound"
    RESTORE = "restore"


class RecoverySignal(BaseModel):
    incident_id: str
    original_model: str
    fallback_model: str

    original_error_rate: float
    baseline_error_rate: float
    original_latency_p95_ms: int
    baseline_latency_p95_ms: int

    quality_score: float | None
    baseline_quality_score: float | None
    drift_detected: bool | None

    model_identity_status: Literal[
        "same_declared_identity",
        "declared_version_changed",
        "fingerprint_changed",
        "behavioral_identity_shift",
        "unknown",
    ]

    clean_windows_count: int
    required_clean_windows: int
    recovery_confidence: float = Field(ge=0, le=1)


class RecoveryCandidate(BaseModel):
    incident_id: str
    route_direction: RouteDirection = RouteDirection.RESTORE
    from_model: str
    to_model: str
    reason: Literal[
        "original_recovered",
        "fallback_ttl_expiring",
        "fallback_cost_too_high",
        "manual_restore_requested",
    ]
    recovery_signal: RecoverySignal
    canary_required: bool = True
    human_required: bool
    proposed_traffic_steps: list[int] = [1, 5, 25, 100]


class RecoveryDecision(BaseModel):
    incident_id: str
    decision_id: str
    action: Literal[
        "restore_canary",
        "stay_on_fallback",
        "require_human",
        "deny_restore",
    ]
    reasons: list[str]
    policy_version: str
    max_allowed_traffic_percent: int
```

### Incident closure

```python
class IncidentClosureContext(BaseModel):
    incident_id: str
    diagnosis_ref: str
    routing_decision_ref: str | None
    execution_result_ref: str
    evidence_refs: list[str]
    timeline_ref: str
    customer_notified: bool
    postmortem_required: bool
    final_state: Literal["RESOLVED", "HUMAN_REVIEW", "CONTAINMENT_MODE"]
```

## Trade-off

One extra monitoring loop, longer flow. Cost is real but the alternative — Otter that fights fires but never extinguishes them — fails the production positioning. The lifecycle gap is what differentiates "auto-router demo" from "governed change control product".

## Cross-refs

- Authorization: `policy-gate.md` (PolicyGate reads RecoveryCandidate, same DMN rule engine)
- Execution: `canary-kill-switch.md` (Canary subprocess accepts `route_direction`)
- Identity acceptance gate: `model-identity.md` (Condition 3)
- Baseline maturity for "quality recovered": `eval-drift-baseline.md`
