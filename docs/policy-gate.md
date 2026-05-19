# PolicyGate — Deterministic Governance Layer

> Sub-doc of `../DESIGN.md`. Owns: DMN-based governance, minimum rule set, Git-backed policy versioning, audit identity.

## Why this is not an LLM agent

PolicyGate decides **whether** a proposed change is allowed under tenant policy, cost cap, data residency, PII handling, rollback availability, and SLA. These decisions must be:

- **Reproducible** — given identical input, identical output, every time.
- **Version-pinned** — every incident record carries the exact policy that decided it.
- **Auditable** — every violation cites the rule that fired.

LLM reasoning fails all three. Otter implements PolicyGate as a **Maestro Business Rule Task (DMN 1.3)**. An optional `PolicyExplainerAgent` may narrate the DMN result in plain language — it has **no authority** to change allow / deny.

## Optimizer vs Governor boundary

| Concern | Optimizer (LLM) | Governor (DMN) |
|---------|-----------------|----------------|
| Component | RoutingDecisionAgent / RecoveryEvaluator | PolicyGate |
| Output | RoutingProposal / RecoveryCandidate (candidate + ranking + ETA) | PolicyDecision (allow_auto / require_canary / require_human / deny) |
| `routing_confidence` | LLM emits | PolicyGate reads, never overrides |
| `policy_allowed` | — | PolicyGate sole authority |
| `max_traffic_percent` | proposes | enforces (may reduce) |

Optimizer **may** read policy hints to avoid proposing obviously illegal candidates. Final authorization always goes through the gate.

## Minimum rule set (v1)

PolicyGate must implement at least:

1. `target_model ∈ allowed_models` AND `∉ denied_models`
2. `target_vendor ∈ allowed_vendors`
3. `requested_region ∈ target_model.allowed_regions`
4. If `pii_detected`: `target_vendor ∈ pii_allowed_vendors` AND `target_model.supports_zero_retention`
5. `projected_incremental_cost_usd ≤ per_incident_cost_cap_usd`
6. `daily_spend + projected ≤ daily_cost_cap_usd`
7. `cost_delta_ratio ≤ max_cost_delta_ratio_auto`, else `require_human`
8. `routing_confidence ≥ min_auto_route_confidence`, else `require_human`
9. `rollback_available == true` for auto route
10. Cross-vendor switch → `require_human` (configurable)
11. `severity == CRITICAL` → `require_human` (configurable)
12. `proposed_traffic_percent > max_direct_shift_percent` → `require_canary`
13. Target not pre-validated for tenant → `require_canary` or `require_human`

Restore-direction adds:

14. Original model identity must match `same_declared_identity`; otherwise `require_human`.
15. Original model `clean_windows_count >= required_clean_windows`.

Loop guard precondition (see `canary-kill-switch.md`):

16. `CircuitBreakerState.auto_route_allowed == true` for current correlation_key.

Baseline maturity precondition (see `eval-drift-baseline.md`):

17. For `trigger_type == proactive_quality`: `baseline.sufficient_for_drift_detection == true`. Otherwise deny / warning_only.

## Git-backed policy versioning

DMN source of truth = **Git**. Orchestrator stores release artifacts only. `policy_version` is composed, not hand-typed.

```
otter.routing_policy@0.3.0+sha.abc1234+dmn.9f31c2
                     │       │           │
                     │       │           └── DMN file SHA-256 (first 6 chars)
                     │       └────────────── Git commit short SHA
                     └────────────────────── Semantic version from manifest.yaml
```

## Repo structure

```
otter/
  policies/
    routing/
      routing_policy.dmn
      routing_policy.tests.yaml
      manifest.yaml
    recovery/
      recovery_policy.dmn
      recovery_policy.tests.yaml
      manifest.yaml
    circuit_breaker/
      circuit_policy.dmn
      circuit_policy.tests.yaml
      manifest.yaml

  model_catalog/
    models.yaml

  baselines/
    baseline_manifest.schema.json

  ci/
    test_dmn.py
    upload_business_rule.py
```

## Manifest format

```yaml
policy_name: otter.routing_policy
semantic_version: 0.3.0
environment: prod
tenant_scope: demo
dmn_file: routing_policy.dmn
dmn_checksum: sha256:9f31c2abc...
git_commit: abc1234
created_by: danny
created_at: 2026-05-19T00:00:00Z
compatible_schema_versions:
  PolicyGateInput: 1.2.0
  PolicyDecision: 1.1.0
```

## Release workflow

W2 minimum (no full CI/CD yet):

1. DMN committed to Git.
2. `semantic_version` / `dmn_checksum` computed and written to manifest.
3. Manual upload to Orchestrator Business Rules.
4. Orchestrator-assigned `business_rule_version` written back to manifest.
5. Runtime PolicyDecision includes `PolicyRuntimeIdentity` (see schema below).

Once Orchestrator's Business Rule version is fixed, **it cannot be edited** — UiPath enforces immutability post-creation, which is exactly what audit needs. New rule revisions get new versions, not in-place edits.

UiPath Automation Ops Source Control can connect GitHub / Azure DevOps repos so the policy folder syncs with Orchestrator. Install the UiPath-AutomationOps GitHub app, authorize the repo, link from Orchestrator side.

## Schema

### Diagnosis + routing proposal (Block 4 inputs)

```python
from typing import Literal
from pydantic import BaseModel, Field
from enum import Enum


class Severity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class DataClass(str, Enum):
    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    CONFIDENTIAL = "CONFIDENTIAL"
    PII = "PII"
    PHI = "PHI"
    FINANCIAL = "FINANCIAL"


class DiagnosisReport(BaseModel):
    incident_id: str
    incident_severity: Severity
    root_cause_hypothesis: str
    confidence: float = Field(ge=0, le=1)
    data_completeness: float = Field(ge=0, le=1)
    customer_impact_summary: str
    evidence_used: list[str]
    evidence_missing: list[str]
    safe_to_auto_route: bool


class RoutingProposal(BaseModel):
    incident_id: str
    tenant_id: str
    from_model: str
    to_model: str
    proposed_vendor: str
    incident_severity: Severity
    change_risk: Severity
    routing_confidence: float = Field(ge=0, le=1)

    expected_quality_delta: float | None = None
    expected_latency_delta_ms: int | None = None
    expected_cost_delta_ratio: float | None = None
    expected_cost_delta_usd: float | None = None

    proposed_traffic_percent: int
    canary_required_by_routing: bool
    canary_plan_id: str | None = None
    diagnosis_ref: str
    evidence_refs: list[str]
```

### PolicyGate input — supporting types

```python
class ModelCatalogEntry(BaseModel):
    model_id: str
    vendor: str
    family: str
    version: str | None = None
    allowed_regions: list[str]
    supports_zero_retention: bool
    supports_no_training_on_data: bool
    data_residency_regions: list[str]
    max_context_tokens: int | None = None
    input_cost_per_1k_tokens_usd: float
    output_cost_per_1k_tokens_usd: float
    baseline_quality_score: float | None = None
    baseline_latency_p95_ms: int | None = None
    prevalidated_for_tenants: list[str] = []
    rollback_targets: list[str] = []


class TenantRoutingPolicy(BaseModel):
    tenant_id: str
    env: Literal["dev", "staging", "prod"]
    policy_version: str

    allowed_models: list[str]
    denied_models: list[str] = []
    allowed_vendors: list[str]
    denied_vendors: list[str] = []

    allowed_data_regions: list[str]
    require_zero_retention_for_pii: bool = True
    require_vendor_dpa_for_pii: bool = True
    pii_allowed_vendors: list[str] = []
    prohibited_data_classes: list[DataClass] = []

    per_incident_cost_cap_usd: float
    daily_cost_cap_usd: float
    monthly_cost_cap_usd: float | None = None
    max_cost_delta_ratio_auto: float = 0.25
    max_cost_delta_ratio_human: float = 2.0

    min_quality_score_auto: float = 0.80
    min_expected_quality_delta_auto: float = 0.0
    max_latency_p95_delta_ms_auto: int = 1500

    min_auto_route_confidence: float = 0.75
    max_direct_shift_percent: int = 0
    max_canary_initial_percent: int = 1
    require_canary_on_model_change: bool = True
    require_human_on_cross_vendor: bool = True
    require_human_on_pii: bool = True
    require_human_on_critical_incident: bool = True

    rollback_required_for_auto: bool = True
    kill_switch_required: bool = True


class RuntimeDataContext(BaseModel):
    data_classes_observed: list[DataClass]
    pii_detected: bool
    requested_region: str
    customer_region: str
    customer_tier: Literal["free", "pro", "enterprise"]
    regulated_customer: bool = False
    sla_minutes_remaining: int | None = None


class RollbackCapability(BaseModel):
    rollback_available: bool
    rollback_target_model: str | None
    rollback_mechanism: Literal[
        "config_flip", "feature_flag", "deployment_rollback", "manual", "none"
    ]
    estimated_rollback_seconds: int | None
    rollback_tested_within_days: int | None


class PolicyGateInput(BaseModel):
    incident_id: str
    tenant_id: str
    env: Literal["dev", "staging", "prod"]

    proposal: RoutingProposal
    tenant_policy: TenantRoutingPolicy
    source_model: ModelCatalogEntry
    target_model: ModelCatalogEntry
    runtime_data_context: RuntimeDataContext
    cost_budget: "CostBudgetSnapshot"   # see cost-aware-routing.md
    rollback: RollbackCapability

    policy_context_fetched_at: str
```

> `CostBudgetSnapshot` lives in `cost-aware-routing.md` — it's authored by the cost domain and consumed here.

### PolicyGate output

```python
class PolicyRuntimeIdentity(BaseModel):
    policy_name: str
    semantic_version: str
    git_commit: str
    dmn_checksum: str
    orchestrator_business_rule_id: str | None
    orchestrator_business_rule_version: str | None
    schema_version: str
    evaluated_at: str


class PolicyViolation(BaseModel):
    code: str
    severity: Literal["blocker", "human_required", "warning"]
    message: str
    evidence_field: str | None = None


class PolicyDecision(BaseModel):
    incident_id: str
    decision_id: str

    action: Literal[
        "allow_auto",
        "require_canary",
        "require_human",
        "deny",
        "no_change",
    ]
    allow_auto_route: bool
    require_canary: bool
    require_human: bool
    deny_reason: str | None = None

    max_allowed_traffic_percent: int
    max_allowed_duration_minutes: int | None = None

    violations: list[PolicyViolation]
    warnings: list[str] = []

    effective_cost_cap_usd: float
    effective_cost_delta_ratio_cap: float
    effective_quality_floor: float
    effective_latency_p95_delta_cap_ms: int

    rollback_required: bool
    rollback_verified: bool

    policy_identity: PolicyRuntimeIdentity
    decision_time: str


class ApprovedRoutingDecision(BaseModel):
    incident_id: str
    decision_id: str
    from_model: str
    to_model: str
    traffic_percent: int
    canary_required: bool
    policy_decision: PolicyDecision
    rollback: RollbackCapability
    canary_plan: dict
    expires_at: str | None
```

`ApprovedRoutingDecision` is what Block 4 hands to Block 5 (Canary). `RecoveryDecision` (see `lifecycle.md`) takes the same shape under `route_direction = "restore"`.

## Trade-off

W2 gains an extra policy release workflow (manifest + checksum + upload script). The cost is real but the alternative — Orchestrator-edited DMN with no audit trail — fails when a customer asks "why did Otter allow this route last Tuesday?". That question must be answerable from the Git history alone.

## Cross-refs

- Inputs: `cost-aware-routing.md` (budget snapshot, ceilings), `lifecycle.md` (restore-direction inputs)
- Loop guard precondition: `canary-kill-switch.md`
- Baseline gate: `eval-drift-baseline.md`
- DMN explainer (optional): see Component Inventory in `../DESIGN.md`
