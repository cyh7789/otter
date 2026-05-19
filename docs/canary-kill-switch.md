# Canary, Kill Switch & Loop Guard

> Sub-doc of `../DESIGN.md`. Owns: Canary subprocess, kill switch dual path, RouteCircuitBreaker, CONTAINMENT_MODE.

## Canary subprocess

Block 5 of the top-level BPMN. Same subprocess serves both outbound and restore routes via `RouteDirection`. Never write a second canary engine.

Owns:

- Progressive traffic shift per severity-driven step plan.
- Two-layer monitoring: **guard window** (rollback decision) + **observation window** (incident close / postmortem escalation).
- Kill switch attached to every step.
- Normal rollback path (internal).

Hands off to the **emergency kill switch process** (separate BPMN) for global / cross-incident actions.

## Severity → window + step plan

| Severity | Guard | Observation | Canary Steps |
|----------|-------|-------------|--------------|
| LOW | 10 min | 60 min | 5% → 25% → 100% |
| MEDIUM | 15 min | 2 hr | 1% → 5% → 25% → 100% |
| HIGH | 30 min | 4 hr | 1% → 5% → 25% → 100% |
| CRITICAL | 60 min | 24 hr | 1% → 5% → 25%; 100% requires human confirm |

Demo: real windows shown in UI, actual waits compressed to 30–90s via demo-mode timer.

## Kill switch threshold pattern

Always compare against baseline, not just absolute. The kill switch answers **"is the new route making things worse?"**, not "is the current state bad?".

```
error_rate
  rollback if rate ≥ max(0.03, baseline + 0.015)
  over 2 consecutive 5-min windows

latency_p95
  rollback if p95 ≥ max(baseline * 1.5, baseline + 1500ms)
  over 2 consecutive windows

quality_score
  rollback if score ≤ baseline - 0.05
  human_confirm if drop is 0.03–0.05

cost
  rollback / pause if projected > per_incident_cap
  human_confirm if projected > auto cap but < human cap

safety / PII
  immediate rollback if safety_violation_rate > 0.01
  immediate rollback if pii_leak_count > 0
```

**Hard stops** — never relaxed even for CRITICAL: PII leakage, safety violations, data residency.

For CRITICAL, error / latency thresholds **may** be relaxed because the original route is already unusable, but safety / PII / residency stay strict.

## Two rollback paths

| Path | Location | Trigger | Authority |
|------|----------|---------|-----------|
| Normal rollback | Inside Canary subprocess | Kill switch threshold fires for THIS incident | Auto if rollback target verified + no PII regression + config-flip mechanism + target not known-bad |
| Emergency kill switch | Separate BPMN process, message start | Global signal (safety incident affecting all tenants) | Human-triggered or trusted automated source, not attached to any single incident |

Normal rollback exits Canary back into the lifecycle's RECOVERY_BLOCKED state if the rollback target is also unhealthy → CONTAINMENT_MODE (below).

Auto vs human-confirm rollback:

| Auto rollback when | Human-confirm when |
|--------------------|---------------------|
| Rollback target verified | Rollback returns to a known-failed model |
| No PII / data residency regression | No recent health data for rollback target |
| Mechanism = config_flip / feature_flag | Evidence is incomplete |
| Incident severity non-CRITICAL OR CRITICAL but rollback target known stable | CRITICAL customer-facing route already at 25%+ |
| | Rollback would cause SLA / compliance / cost secondary damage |

## RouteCircuitBreaker — Loop Guard

**Type**: deterministic state machine (not LLM). Position: **before** RoutingDecisionAgent in BPMN. PolicyGate also reads its decision (see `policy-gate.md` rule 16).

Without this, auto-remediation loops:

```
original model fails
  → route B
  → B canary fails
  → rollback to original
  → original still failing
  → trigger pipeline again
  → route B again ← loop
```

This is the most common runaway mode in auto-remediation systems. The guard makes it deterministic to break.

## Three-layer retry budget

| Scope | Default |
|-------|---------|
| Same target model retry cooldown | 30 min |
| Same target model max attempts | 2 / 24 hr |
| Incident-level total route attempts | 3 |
| Correlation-key auto-route cooldown after repeated failure | 60 min |
| Half-open canary size | 1% |
| Critical incident override | human only |

## Circuit states

```
CLOSED
  Normal — auto-routing allowed.

OPEN
  Auto-route disabled. Allows:
    - evidence attach
    - notification
    - human review
  Disallows:
    - new RoutingDecisionAgent runs
    - new RecoveryEvaluator runs

HALF_OPEN
  After cooldown. Allow ONE small canary (e.g. 1%).
  Success → CLOSED.
  Failure → OPEN.
```

## CONTAINMENT_MODE

When rollback target is **also** unhealthy, rollback is not a real option. Canary subprocess routes to CONTAINMENT_MODE instead of attempting another route:

1. Stop auto-routing.
2. Hold the current least-risky route (whichever it currently is).
3. Throttle / reduce traffic where possible.
4. Activate fallback response / cached results / degraded UX.
5. Page human immediately.

CONTAINMENT_MODE exits only via Action Center decision, not via automated recovery.

## Schema

```python
from typing import Literal
from pydantic import BaseModel


class RouteAttemptRecord(BaseModel):
    incident_id: str
    correlation_key: str
    from_model: str
    to_model: str
    route_direction: Literal["outbound", "restore"]
    attempt_no: int
    started_at: str
    ended_at: str | None
    status: Literal["passed", "failed", "rolled_back", "aborted"]
    failure_reason: str | None
    canary_max_percent: int
    policy_version: str
    decision_id: str


class CircuitBreakerState(BaseModel):
    correlation_key: str
    state: Literal["closed", "open", "half_open"]
    opened_at: str | None
    half_open_after: str | None
    open_reason: str | None

    failed_targets: dict[str, int]
    total_route_attempts_24h: int
    last_failed_target: str | None
    last_failure_at: str | None

    auto_route_allowed: bool
    human_required: bool
    excluded_targets: list[str]


class LoopGuardDecision(BaseModel):
    incident_id: str
    correlation_key: str
    allow_auto_route: bool
    allow_human_route: bool
    excluded_models: list[str]
    max_candidate_count: int
    reason: str
    circuit_state: CircuitBreakerState


class MetricThreshold(BaseModel):
    metric: Literal[
        "error_rate",
        "quality_score",
        "latency_p95_ms",
        "cost_per_1k_requests_usd",
        "safety_violation_rate",
        "pii_leak_count",
    ]
    comparison: Literal[">", ">=", "<", "<="]
    absolute_threshold: float | None = None
    baseline_delta_threshold: float | None = None
    relative_delta_threshold: float | None = None
    consecutive_windows_required: int = 1
    window_minutes: int = 5
    severity: Literal["rollback", "human_confirm", "warn"]


class PostRouteMonitorPolicy(BaseModel):
    incident_id: str
    decision_id: str
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    guard_window_minutes: int
    observation_window_minutes: int
    canary_steps: list[int]
    thresholds: list[MetricThreshold]
    auto_rollback_enabled: bool
    human_confirm_required_for_full_cutover: bool


class MonitoringSnapshot(BaseModel):
    incident_id: str
    decision_id: str
    traffic_percent: int
    window_started_at: str
    window_ended_at: str
    error_rate: float
    baseline_error_rate: float
    quality_score: float | None
    baseline_quality_score: float | None
    latency_p95_ms: int
    baseline_latency_p95_ms: int
    cost_per_1k_requests_usd: float
    safety_violation_rate: float | None
    pii_leak_count: int | None


class KillSwitchDecision(BaseModel):
    incident_id: str
    decision_id: str
    triggered: bool
    action: Literal["continue", "rollback", "pause_and_human_confirm", "reduce_traffic"]
    triggered_conditions: list[str]
    confidence: float
    snapshot_ref: str


class ChangeExecutionResult(BaseModel):
    incident_id: str
    decision_id: str
    execution_status: Literal[
        "not_started",
        "canary_running",
        "passed",
        "rolled_back",
        "failed",
        "containment_mode",
    ]
    final_model: str
    max_traffic_percent_reached: int
    rollback_performed: bool
    rollback_reason: str | None
```

## Trade-off

RoutingDecisionAgent's candidate space shrinks under the circuit breaker — a model that "just failed but is fine now" may be excluded. This is the correct default for auto-remediation. Operators can override via Action Center if they have out-of-band knowledge.

## Cross-refs

- Authority over restore canary: `lifecycle.md` (RouteDirection)
- Authorization for every route attempt: `policy-gate.md`
- Identity check before reusing a recently-failed target: `model-identity.md`
- Baseline-derived thresholds: `eval-drift-baseline.md`
