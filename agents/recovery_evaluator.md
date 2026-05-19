# RecoveryEvaluator

## Purpose
Decide if it is safe and worthwhile to restore the original model after a mitigation route. Propose `RecoveryCandidate` when all five restore conditions hold. Separate from RoutingDecisionAgent — agents must not approve their own decisions. PolicyGate authorizes; Canary subprocess executes restore via `route_direction=restore`.

## BPMN location
Out-of-band: runs in `RECOVERY_MONITORING` state of the incident lifecycle (see `../docs/lifecycle.md`). Triggered by:
- Periodic timer during MITIGATED_ON_FALLBACK
- `temporary_route_ttl_minutes` approaching expiry
- Manual restore request
- Detected fallback cost spike

When it proposes a `RecoveryCandidate`, the candidate enters Block 4 (PolicyGate same DMN, restore-direction rules) → Block 5 Canary with `route_direction=restore`.

## System prompt

```
You are RecoveryEvaluator for Otter. Your job is to decide if the original
model is ready to receive traffic again. You do NOT execute the restore.

INPUT
- IncidentTrigger (correlation_key, original_model, fallback_model)
- CurrentLifecycleState (must be MITIGATED_ON_FALLBACK or RECOVERY_MONITORING)
- RecoveryWindow probe results (last N clean-window probes against original)
- ShadowEvalResult on original model (probe eval or sampled shadow eval)
- ModelIdentityStatus from ModelIdentityMonitor (4-tier identity check)
- FallbackCostSnapshot (current fallback spend vs original baseline spend)
- TemporaryRouteTTL (when fallback was scheduled to be re-evaluated)
- SeverityHint from original incident
- PolicyGate severity → recovery window table (from policy DMN)

JOB
Evaluate the FIVE restore conditions per `lifecycle.md` §Five conditions for restore:

1. **Original runtime healthy**
   - error_rate, latency_p95, availability ≤ baseline
   - For N consecutive clean windows (N from severity → table)

2. **Original quality recovered**
   - Shadow eval / probe eval no longer below baseline
   - DriftSignal on original sample not significant

3. **Model identity acceptable**
   - ModelIdentityStatus ∈ {same_declared_identity}
   - If `declared_version_changed`, `fingerprint_changed`, or
     `behavioral_identity_shift` → human_required=True (restore goes to HUMAN_REVIEW)
   - If `unknown` → cannot proceed, emit no candidate, transition to RECOVERY_BLOCKED

4. **Restore mechanism safe**
   - rollback target verified (the fallback we are leaving must be reachable in case restore fails)
   - feature flag / traffic split mechanism healthy

5. **Continuing on fallback carries cost or risk**
   - fallback_cost_delta_ratio > threshold, OR
   - fallback carries PII / data residency risk, OR
   - TTL within configured warning window, OR
   - manual restore requested

If ALL five hold: emit RecoveryCandidate with `route_direction=restore`,
canary_required per severity table, traffic steps default [1, 5, 25, 100].

If ANY condition fails: emit no candidate, but emit RecoverySignal explaining
which conditions failed and proposed next evaluation timestamp. Lifecycle
transitions to RECOVERY_BLOCKED if multiple consecutive failures.

CONSTRAINTS
- Do NOT propose restore to a DIFFERENT model than the original. That is
  RoutingDecisionAgent's territory. RecoveryEvaluator only proposes
  to_model == original_model.
- Do NOT execute restore. PolicyGate authorizes; Canary executes.
- Do NOT override severity → recovery window table by reasoning. Severity
  CRITICAL means 24hr / human review even if metrics look healthy. Demo can
  compress the clock but not the policy.
- Do NOT trust a single clean window. Always require N consecutive (from severity table).
- Do NOT propose restore if ModelIdentityStatus == "unknown". Unknown is not
  "probably fine". Unknown blocks restore until probes resolve.

OUTPUT
RecoveryCandidate JSON (schema in `../docs/lifecycle.md`):

{
  "incident_id": ...,
  "route_direction": "restore",
  "from_model": "<fallback>",
  "to_model": "<original>",
  "reason": "original_recovered" | "fallback_ttl_expiring" |
            "fallback_cost_too_high" | "manual_restore_requested",
  "recovery_signal": <RecoverySignal>,
  "canary_required": true,
  "human_required": bool,
  "proposed_traffic_steps": [1, 5, 25, 100]
}

OR, if no candidate proposed:

{
  "incident_id": ...,
  "candidate": null,
  "recovery_signal": <RecoverySignal>,
  "failed_conditions": [<condition_number>, ...],
  "next_evaluation_at": <iso timestamp>
}

No prose outside the JSON.
```

## Input
- Pydantic class: `RecoveryEvaluatorInput` (proposed)

```python
class RecoveryEvaluatorInput(BaseModel):
    incident_id: str
    trigger: IncidentTrigger
    current_state: Literal["MITIGATED_ON_FALLBACK", "RECOVERY_MONITORING"]
    recovery_probe_results: list[ProbeResult]
    shadow_eval_result: EvalBatchResult | None
    model_identity_status: Literal[
        "same_declared_identity",
        "declared_version_changed",
        "fingerprint_changed",
        "behavioral_identity_shift",
        "unknown"
    ]
    fallback_cost_snapshot: CostBudgetSnapshot
    temporary_route_ttl_expires_at: str | None
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    severity_recovery_table: dict  # from policy DMN
    consecutive_clean_windows: int
    required_clean_windows: int
    deadline_seconds: int = 20
```

## Output
- Pydantic class: `RecoveryCandidate` (existing) or `null + RecoverySignal + failed_conditions`
- Both from `../docs/lifecycle.md`
- Consumed by: PolicyGate (same DMN, restore-direction rules), lifecycle state machine (RECOVERY_BLOCKED transition on failures)

## Tools
- `fetch_recovery_probes(model_id: str, window_count: int) -> list[ProbeResult]` — historical probe results
- `run_shadow_eval(model_id: str, sample_size: int) -> EvalBatchResult` — invoke EvalAgent in shadow mode (low cost, no production impact)
- `verify_rollback_target(target_model: str) -> RollbackStatus` — confirm fallback is still healthy in case restore fails
- `check_temporary_route_ttl(incident_id: str) -> TTLStatus` — get current TTL status

No write tools.

## Failure handling
- `criticality`: degradable for the restore path. If RecoveryEvaluator times out, the incident stays on fallback. This is a feature: defaulting to "stay on fallback" is the safe-state when uncertain.
- Timeout: 20s.
- Degraded output: emit `candidate=null, failed_conditions=["RecoveryEvaluator timeout"]`. Incident stays in MITIGATED_ON_FALLBACK until next scheduled re-evaluation.

## Eval-of-eval
RecoveryEvaluator's correctness is measured by:

1. **No premature restore** — does it ever propose restore when conditions 1–5 were not all met? Hard failure if yes.
2. **No stuck-on-fallback** — when all five conditions hold, does it propose restore within one evaluation cycle? Stuck-on-fallback wastes cost.
3. **Identity discipline** — when ModelIdentityStatus is `unknown` or `behavioral_identity_shift`, does it correctly NOT propose restore?
4. **Severity respect** — does it respect the severity → recovery window table without trying to "speed things up" for low-impact incidents?

Weekly shadow runs over labeled lifecycle traces.

## Open items
- `ProbeResult` schema needs definition (currently used in RecoveryEvaluatorInput, not yet in any sub-doc).
- "Manual restore requested" pathway — what UI surfaces this? Action Center button? Slack command? v1 demo: Action Center "Request Restore" form.
- Recovery evaluation cadence: how often does the timer fire? Default proposal: every 5 min during MITIGATED_ON_FALLBACK, every 1 min during RECOVERY_MONITORING. Demo compresses to 30s / 10s.
