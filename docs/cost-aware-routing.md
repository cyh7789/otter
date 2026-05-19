# Cost-Aware Routing

> Sub-doc of `../DESIGN.md`. Owns: 3-layer cost ceiling, route classification, temporary route TTL.

## Why split optimizer from governor on cost

Cost-aware routing is the most tempting place to use a single weighted score: "quality × 0.4 + cost × 0.3 + latency × 0.3". This breaks in production:

- "Spend 10× to recover 1% quality" — weighted score allows it; common sense says no.
- "Save 20% cost but lose enterprise SLA" — weighted score allows it; contracts forbid it.

Otter splits cost handling cleanly:

| Role | Component | Concern |
|------|-----------|---------|
| Optimizer | RoutingDecisionAgent | Estimate cost delta, rank candidates on Pareto frontier |
| Governor | PolicyGate | Enforce hard caps, contract limits, approval rules |

## Three-layer ceiling

```
per-incident
  Cap on firefighting spend for ONE incident.
  Prevents a runaway agent from burning $5,000 to "fix" a $50 outage.

per-day
  Stop flapping / repeated routes from blowing the daily budget.
  Important guardrail for the circuit-breaker era — even valid routes accumulate.

per-tenant / per-month (optional v1)
  Contract / quota boundary.
  Prevents one tenant from consuming all of the shared budget.
```

v1 implements:

```
per_incident_cost_cap_usd
daily_cost_cap_usd
max_cost_delta_ratio_auto
max_cost_delta_ratio_human
```

Full billing system is not needed for the hackathon demo.

## Route classification

RoutingDecisionAgent emits, PolicyGate enforces:

| Type | Quality | Cost | Default action |
|------|---------|------|----------------|
| Quality rescue | ↑ | ↑ | Allow on HIGH/CRITICAL with TTL + canary |
| Cost saving | flat / slight ↓ | ↓ | Allow on LOW non-critical traffic only |
| Latency rescue | flat | slight ↑ | Auto-canary |
| Unsafe economy | ↓ | ↓ | Deny or require human |

Hard rule: **do not use a cheaper-quality model to "fix" a quality incident.** This is the most common bad-actor automation path; PolicyGate must reject it.

## Quality rescue conditions

Allow expensive route only when all hold:

1. `incident_severity ≥ HIGH`
2. `expected_quality_delta ≥ policy.min_quality_improvement_for_expensive_route`
3. `expected_cost_delta_ratio ≤ max_cost_delta_ratio_human` (or explicit human approval)
4. Target model passes data residency / PII / DPA checks
5. Rollback available
6. Route carries a TTL — e.g. 2 hours or until incident closes

`temporary_route_ttl_minutes` is mandatory for quality-rescue routes. Without it, temporary rescue becomes permanent cost inflation.

Note: TTL expiry only triggers **re-evaluation** (cost / health / restore-candidate generation), never auto-switchback. See `lifecycle.md`.

## Cost-saving conditions

Allow cheaper route only when all hold:

1. `incident_severity ≤ MEDIUM`
2. Target quality lower bound ≥ tenant quality floor
3. No PII / regulated workload risk
4. Canary passes
5. Customer tier non-enterprise, or enterprise policy explicitly allows

Never apply cost-saving routing while handling a quality incident. The optimizer must filter these out before they reach PolicyGate.

## PolicyGate cost enforcement

```
if expected_cost_delta_ratio > max_cost_delta_ratio_auto:
    require_human

if projected_incremental_cost_usd > per_incident_cost_cap_usd:
    deny  OR  require_human  (depends on severity)

if daily_spend + projected_cost > daily_cost_cap_usd:
    deny  unless  (severity == CRITICAL AND human override)

if target_quality_score < tenant_quality_floor:
    deny  even if cheaper
```

## BPMN placement

```
RoutingDecisionAgent
  → EstimateCostImpact         (LLM agent or API call to cost-model service)
  → FetchBudgetSnapshot         (API to spend tracker)
  → PolicyGate                  (DMN Business Rule Task)
  → Exclusive Gateway:
      allow_auto
      require_human_cost_override
      deny_budget_exceeded
```

Cost estimate is RoutingDecisionAgent's input data. Cost ceiling enforcement is PolicyGate's authority. Never let the LLM agent decide whether the budget is "OK to break for this one".

## Schema

```python
from typing import Literal
from pydantic import BaseModel, Field


class CostAwareRoutePolicy(BaseModel):
    max_cost_delta_ratio_auto: float
    max_cost_delta_ratio_human: float
    min_quality_improvement_for_expensive_route: float
    expensive_route_allowed_severities: list[Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]]
    temporary_route_ttl_minutes: int
    require_human_for_cost_delta_above: float


class CostBudgetSnapshot(BaseModel):
    tenant_id: str
    window_started_at: str
    daily_spend_so_far_usd: float
    monthly_spend_so_far_usd: float | None = None
    incident_spend_so_far_usd: float = 0.0
    projected_incremental_cost_usd: float
    projected_cost_delta_ratio: float


class CostImpactEstimate(BaseModel):
    incident_id: str
    from_model: str
    to_model: str
    estimated_requests: int
    estimated_input_tokens: int
    estimated_output_tokens: int
    current_cost_usd: float
    proposed_cost_usd: float
    incremental_cost_usd: float
    cost_delta_ratio: float
    estimate_confidence: float = Field(ge=0, le=1)


class CostPolicyResult(BaseModel):
    within_per_incident_cap: bool
    within_daily_cap: bool
    within_monthly_cap: bool | None
    requires_human_cost_override: bool
    max_allowed_duration_minutes: int | None
    reasons: list[str]


class RouteUtilityEstimate(BaseModel):
    candidate_model: str
    expected_quality_delta: float
    expected_latency_delta_ms: int
    expected_error_rate_delta: float
    expected_cost_delta_usd: float
    expected_cost_delta_ratio: float
    confidence: float
    customer_impact_reduction_score: float
    pareto_rank: int
    dominated_by: list[str] = []
```

## Trade-off

3-layer ceiling adds three policy fields the customer must configure. The alternative — single budget — fails when the customer has both daily caps AND per-incident caps AND tenant quotas. v1 ships with sensible defaults so demo tenants only configure what they care about.

## Cross-refs

- Enforced by: `policy-gate.md` (rules 5, 6, 7)
- TTL expiry behavior: `lifecycle.md` (re-evaluation, not switchback)
- Daily-cap interaction with retry budget: `canary-kill-switch.md` (circuit breaker stops loop-cost burn)
