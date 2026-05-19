# Schemas — Retired

> This file used to hold every Pydantic schema in one place. Maintenance got unwieldy as the schema set crossed 30 models. **Schemas are now embedded in the sub-doc that owns each domain.**

## Where to find schemas now

| Type | Lives in |
|------|----------|
| `RawTriggerEnvelope`, `IncidentTrigger`, `EvidencePlan`, `EvalRequest`, `IncidentClosureContext` | [`lifecycle.md`](lifecycle.md) |
| `RouteDirection`, `RecoverySignal`, `RecoveryCandidate`, `RecoveryDecision` | [`lifecycle.md`](lifecycle.md) |
| `Severity`, `DataClass`, `DiagnosisReport`, `RoutingProposal` | [`policy-gate.md`](policy-gate.md) |
| `ModelCatalogEntry`, `TenantRoutingPolicy`, `RuntimeDataContext`, `RollbackCapability`, `PolicyGateInput` | [`policy-gate.md`](policy-gate.md) |
| `PolicyRuntimeIdentity`, `PolicyViolation`, `PolicyDecision`, `ApprovedRoutingDecision` | [`policy-gate.md`](policy-gate.md) |
| `RouteAttemptRecord`, `CircuitBreakerState`, `LoopGuardDecision` | [`canary-kill-switch.md`](canary-kill-switch.md) |
| `MetricThreshold`, `PostRouteMonitorPolicy`, `MonitoringSnapshot`, `KillSwitchDecision`, `ChangeExecutionResult` | [`canary-kill-switch.md`](canary-kill-switch.md) |
| `EvidencePacket`, `EvidenceBundle` | [`eval-drift-baseline.md`](eval-drift-baseline.md) |
| `EvalBatchResult`, `DriftSignal`, `JudgeRunIdentity`, `JudgeCalibrationReport`, `JudgeHealthSignal`, `EvalDriftReport` | [`eval-drift-baseline.md`](eval-drift-baseline.md) |
| `BaselineProfile`, `BaselineReadinessDecision` | [`eval-drift-baseline.md`](eval-drift-baseline.md) |
| `ProviderIdentitySnapshot`, `ProbeResult`, `BehavioralFingerprint`, `ModelIdentityDriftSignal` | [`model-identity.md`](model-identity.md) |
| `CostAwareRoutePolicy`, `CostBudgetSnapshot`, `CostImpactEstimate`, `CostPolicyResult`, `RouteUtilityEstimate` | [`cost-aware-routing.md`](cost-aware-routing.md) |

See [`README.md`](README.md) for the sub-doc layout rationale and dependency map.
