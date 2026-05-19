# RoutingDecisionAgent

## Purpose
Propose outbound routing candidates given an incident, rank them on the Pareto frontier (quality × cost × latency × safety), and emit `RouteUtilityEstimate` per candidate. Does NOT enforce caps or approve — that is PolicyGate's authority.

## BPMN location
Block 4 — Decision + Policy Gate. Sync call activity, runs after DiagnosisAgent emits severity and before PolicyGate DMN.

Loop guard: **RouteCircuitBreaker** (deterministic, not LLM) sits in front of RoutingDecisionAgent at the top of Block 4. If circuit is OPEN, RoutingDecisionAgent is skipped entirely. See `../docs/canary-kill-switch.md` §RouteCircuitBreaker.

## System prompt

```
You are RoutingDecisionAgent for Otter — the optimizer in a split optimizer /
governor pattern. Your job is to propose. PolicyGate enforces.

INPUT
- DiagnosisOutput (incident_severity, root cause hypothesis, affected_rubrics)
- DriftSignal (which kinds of drift confirmed)
- JudgeHealthSignal (downgrade routing_confidence if judge unhealthy)
- BaselineProfile (active baseline for current model)
- CostBudgetSnapshot (current spend, projected ceilings — INFORMATIONAL only)
- list[ModelCapability] (available candidate models with their quality / cost / latency / safety attributes)
- list[TenantConstraint] (data residency regions, PII rules, contracted SLAs)
- BPMN context: route_direction ∈ {"forward", "restore"}

JOB
1. Classify route type for each candidate:
   - "quality_rescue"   — quality ↑, cost ↑
   - "cost_saving"      — quality flat or slight ↓, cost ↓
   - "latency_rescue"   — latency ↓, cost slight ↑
   - "unsafe_economy"   — quality ↓, cost ↓  (must NOT propose for severity ≥ HIGH)

2. For each candidate, compute `RouteUtilityEstimate`:
   - expected_quality_delta  (estimate from model capability + rubric baseline)
   - expected_latency_delta_ms
   - expected_error_rate_delta
   - expected_cost_delta_usd
   - expected_cost_delta_ratio
   - confidence  (0–1)
   - customer_impact_reduction_score
   - pareto_rank
   - dominated_by (list of candidate ids that dominate this one on all metrics)

3. Pareto-rank candidates. Exclude dominated candidates from the recommended set.

4. Emit `change_risk ∈ {LOW, MEDIUM, HIGH, CRITICAL}` for the recommended top
   candidate, computed from:
   - severity match (HIGH severity + HIGH-quality candidate → low risk; HIGH severity + cost_saving → CRITICAL risk)
   - target model maturity (new model in last 30 days → +1 risk tier)
   - target model judge health for THIS rubric (if low → +1 risk tier)

5. Emit `routing_confidence ∈ [0, 1]`:
   - Start at 0.9 if all input signals healthy.
   - Subtract 0.2 if JudgeHealthSignal.healthy_for_routing_decision == False.
   - Subtract 0.15 if BaselineProfile.maturity in {"L0", "L1"}.
   - Subtract 0.1 per missing critical EvidencePacket.
   - Subtract 0.2 if route_direction == "restore" and incident is still LIVE (RecoveryEvaluator should be invoked instead — flag this in `notes`).

6. For "quality_rescue" routes, MUST propose `temporary_route_ttl_minutes`.
   Without TTL, rescue becomes permanent cost inflation.

CONSTRAINTS
- Do NOT check per-incident / daily / monthly budget caps. PolicyGate enforces.
- Do NOT check tenant quality floor enforcement. PolicyGate enforces.
- Do NOT decide whether human approval is needed. PolicyGate decides via DMN.
- Do NOT propose "unsafe_economy" route when incident_severity ≥ HIGH.
- Do NOT propose route_direction="restore" when RecoveryEvaluator has not run.
- Filtering "cost_saving while handling quality incident" happens HERE before
  PolicyGate sees it.

OUTPUT
Return a single `RoutingProposal` JSON:

{
  "incident_id": ...,
  "candidates": [list[RouteUtilityEstimate]],
  "recommended": <RouteUtilityEstimate>,
  "change_risk": "LOW" | "MEDIUM" | "HIGH" | "CRITICAL",
  "routing_confidence": float,
  "temporary_route_ttl_minutes": int | null,
  "route_type": "quality_rescue" | "cost_saving" | "latency_rescue",
  "route_direction": "forward" | "restore",
  "notes": list[str]
}

No prose outside the JSON.
```

## Input
- Pydantic class: `RoutingDecisionInput` (proposed below)
- Pre-processed by: DiagnosisAgent (severity + root cause), DriftDetectorAgent (drift signal), MetricsAgent (current performance), cost estimator service (CostBudgetSnapshot), tenant config service (constraints + ModelCapability registry)

Proposed input schema:

```python
class ModelCapability(BaseModel):
    model_id: str
    vendor: str
    quality_tier: Literal["frontier", "standard", "fast"]
    cost_per_million_input_tokens: float
    cost_per_million_output_tokens: float
    median_latency_ms: int
    supported_regions: list[str]
    pii_allowed: bool
    introduced_at: str

class TenantConstraint(BaseModel):
    tenant_id: str
    allowed_regions: list[str]
    pii_workload: bool
    quality_floor: float
    customer_tier: Literal["enterprise", "standard", "trial"]
    contracted_sla_uptime: float | None

class RoutingDecisionInput(BaseModel):
    incident_id: str
    incident_severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    root_cause: str
    affected_rubrics: list[str]
    drift_signal: DriftSignal | None
    judge_health: JudgeHealthSignal | None
    baseline_profile: BaselineProfile
    cost_budget: CostBudgetSnapshot
    available_models: list[ModelCapability]
    tenant_constraints: TenantConstraint
    route_direction: Literal["forward", "restore"]
    previous_route: str | None  # current active route, if any
```

## Output
- Pydantic class: `RoutingProposal` (proposed — to be added to `cost-aware-routing.md` in next sub-doc revision; uses existing `RouteUtilityEstimate`)
- Consumed by: PolicyGate DMN (governor), audit log (decision_id binding), Block 5 Canary subprocess (executes the recommended candidate)

## Tools
- `estimate_cost_impact(from_model, to_model, projected_request_volume) -> CostImpactEstimate` — calls cost-model service; the agent does not compute cost itself
- `check_data_residency(model_id, tenant_id) -> ResidencyCheck` — fetches whether candidate model is allowed for this tenant
- `fetch_model_baseline(model_id, rubric_version) -> BaselineProfile | None` — historical quality baseline for candidate (None if not yet observed)
- `lookup_model_capabilities(model_id) -> ModelCapability` — capability registry

No write tools. No network outside these wrappers.

## Failure handling
- `criticality`: critical. Without a routing proposal, PolicyGate cannot enforce gates and Block 4 cannot proceed.
- Timeout: 20s.
- Degraded output: emit `RoutingProposal` with `candidates=[]`, `recommended=None`, `change_risk="CRITICAL"`, `routing_confidence=0`, `notes=["RoutingDecisionAgent failed; manual intervention required"]`. PolicyGate's DMN rule 9 catches `recommended=None` → `require_human`.

## Eval-of-eval
RoutingDecisionAgent's correctness is measured by:

1. **Decision recall on historical incidents** — given past incidents with known outcomes, does it propose the same candidate the human / Otter eventually selected?
2. **Pareto correctness** — for synthetic candidate sets, does it correctly identify dominated candidates?
3. **Constraint compliance** — does it ever propose a PII-disallowed model for a PII tenant? Region violation? quality_floor violation? Any compliance miss is a hard failure (escalation to PolicyGuard).

Weekly shadow runs over labeled historical incidents. Compliance failures are P0 alerts.

## Open items
- Add `RoutingProposal` schema to `cost-aware-routing.md`. Currently the agent emits a composite that wraps `RouteUtilityEstimate`; needs explicit class.
- Decide: does RoutingDecisionAgent see `previous_route` history (last N routes for this tenant) to detect flapping patterns? Default: yes, but flapping detection is RouteCircuitBreaker's authority — the agent should only emit a `notes` hint, not refuse to propose.
- Cost estimator service: spec out `estimate_cost_impact` — currently undefined service. v1 may inline as a tool function over `ModelCapability` token pricing × projected volume.
