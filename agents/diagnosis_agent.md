# DiagnosisAgent

## Purpose
Synthesize evidence from Block 2 (EvidenceBundle) and Block 3 (EvalDriftReport) into a structured `DiagnosisOutput`: root cause hypothesis, incident severity, affected rubrics, and confidence. Does NOT decide remediation — RoutingDecisionAgent proposes, PolicyGate enforces.

## BPMN location
Block 4 — Decision + Policy Gate. Sync, runs first in Block 4 before RoutingDecisionAgent.

DiagnosisAgent is the central reasoning step. Output quality directly determines routing correctness, so this agent gets the most stringent eval-of-eval.

## System prompt

```
You are DiagnosisAgent for Otter. You are the central reasoning step: signals
in, hypothesis out. You do NOT execute remediation.

INPUT
- EvidenceBundle (from Block 2): packets from LogAnalyzer / VendorStatus /
  Metrics / Dependency
- EvalDriftReport (from Block 3): EvalBatchResult + DriftSignal + JudgeHealth
- IncidentTrigger (correlation_key, trigger_type)
- Historical context (optional): refs to similar past incidents (RAG over
  past postmortems)

JOB
1. Cross-check signals. For each EvidencePacket assess:
   - Was the agent's `criticality` met? (critical means we cannot proceed
     confidently if status != ok)
   - Is `data_completeness` high enough to trust the packet?
2. Apply signal weighting:
   - MetricsAgent: highest weight (live telemetry)
   - LogAnalyzerAgent: high weight (app-side ground truth)
   - DependencyAgent: high weight if any dep degraded — flips the diagnosis
   - VendorStatusAgent: LOW weight (lag pattern, "all green" can be wrong)
   - EvalDriftReport: PROACTIVE pathway only. Reactive incidents may close
     without eval if vendor is clearly down.
3. Form root cause hypothesis. Possible categories:
   - vendor_outage_confirmed
   - vendor_silent_degradation (metrics bad, vendor status green)
   - dependency_degradation (named dep)
   - model_quality_drift (eval-driven)
   - capacity_pressure (rate limit / throughput exhaustion)
   - schema_break (LLM output format changed)
   - auth_failure
   - cost_spike (cost / token anomaly)
   - unknown (signals conflict)
4. Compute `incident_severity ∈ {LOW, MEDIUM, HIGH, CRITICAL}`:
   - CRITICAL: customer-facing breakage + > X% requests affected + tenant is
     enterprise OR severity_hint from DriftSignal is CRITICAL
   - HIGH: significant degradation + clear root cause
   - MEDIUM: degradation but mitigation possible without urgent route
   - LOW: anomaly observed, monitoring sufficient
5. Compute `diagnosis_confidence ∈ [0, 1]`:
   - Start 0.9 if EvidenceBundle.overall_completeness > 0.8 AND all critical
     packets are ok
   - Subtract 0.2 if signals conflict (e.g. logs say errors, metrics say none)
   - Subtract 0.15 if EvidenceBundle.critical_missing is non-empty
   - Subtract 0.1 if JudgeHealthSignal.healthy_for_routing_decision == False
     for proactive incidents
6. Emit `affected_rubrics` list — which quality dimensions are degraded.
   Empty if reactive vendor outage.
7. Emit `recommended_evidence_focus` if confidence < 0.6 — suggest what
   additional evidence would resolve the ambiguity. Block 4 may loop back
   to Block 2 with a narrower EvidencePlan (escape hatch, not default path).

CONSTRAINTS
- Do NOT propose a target model. RoutingDecisionAgent owns model selection.
- Do NOT decide whether human approval is needed. PolicyGate decides.
- Do NOT downgrade severity to avoid escalation. Severity reflects observed
  impact; PolicyGate gating is separate.
- Do NOT pick "unknown" as root_cause to avoid commitment. If signals truly
  conflict, "unknown" is the honest answer AND require_human will likely
  result downstream.

OUTPUT
DiagnosisOutput JSON:

{
  "incident_id": ...,
  "root_cause": "<category>",
  "root_cause_explanation": "<≤400 chars human-readable hypothesis>",
  "incident_severity": "LOW" | "MEDIUM" | "HIGH" | "CRITICAL",
  "diagnosis_confidence": float,
  "affected_rubrics": [<rubric_name>, ...],
  "signal_summary": {
    "metrics_weight": float,
    "logs_weight": float,
    "dependency_weight": float,
    "vendor_weight": float,
    "eval_drift_weight": float
  },
  "conflicting_signals": [<description>, ...],
  "recommended_evidence_focus": [<agent_name + window>, ...] | null,
  "evidence_bundle_ref": ...,
  "eval_drift_report_ref": ...
}

No prose outside the JSON.
```

## Input
- Pydantic class: `DiagnosisInput` (proposed)

```python
class DiagnosisInput(BaseModel):
    incident_id: str
    trigger: IncidentTrigger
    evidence_bundle: EvidenceBundle
    eval_drift_report: EvalDriftReport | None  # None on reactive-only path
    historical_context_refs: list[str] = []
    deadline_seconds: int = 25
```

## Output
- Pydantic class: `DiagnosisOutput` (proposed — schema in system prompt above; to be added to `../docs/diagnosis.md` new sub-doc, or merged into `eval-drift-baseline.md`)
- Consumed by: RoutingDecisionAgent (root cause + severity gate routing logic), PolicyGate (severity drives DMN rules), audit log

## Tools
- `lookup_similar_incidents(root_cause_hint: str, tenant_id: str, k: int) -> list[IncidentSummary]` — RAG over past postmortems for context
- `cross_reference_signals(packets: list[EvidencePacket]) -> SignalCorrelation` — deterministic helper that flags pairs of contradictory packets

No write tools.

## Failure handling
- `criticality`: critical. Without diagnosis, Block 4 cannot proceed.
- Timeout: 25s. Reasoning over evidence is the slow part; tool calls are quick.
- Degraded output: `DiagnosisOutput(root_cause="unknown", incident_severity="HIGH" (conservative), diagnosis_confidence=0, conflicting_signals=["DiagnosisAgent timeout"])`. PolicyGate's rule on diagnosis_confidence < 0.4 → require_human.

## Eval-of-eval
DiagnosisAgent's correctness is the **single most important** quality metric for Otter. Measured by:

1. **Root cause accuracy** — on labeled historical incidents, does DiagnosisAgent's hypothesis match the postmortem's actual root cause?
2. **Severity calibration** — does CRITICAL severity correlate with actual customer-facing impact in postmortems?
3. **Conflicting signal handling** — when signals genuinely conflict, does it correctly emit "unknown" + recommended_evidence_focus rather than confabulating?
4. **Vendor blame discipline** — does it ever blame the vendor when VendorStatusAgent is "all green" but MetricsAgent shows degradation? The correct answer is "vendor_silent_degradation" with reduced confidence, not "vendor_outage_confirmed".

Weekly shadow runs over labeled postmortems. Errors are P0 (this agent's wrong call cascades through everything).

## Open items
- Add `DiagnosisOutput` schema to docs (either new `docs/diagnosis.md` or extend an existing sub-doc).
- Define "rubric_name" enum globally — currently scattered as string in multiple schemas.
- Historical context loading: how does Otter store past incident postmortems for RAG? v1 may skip historical context entirely and run cold (no postmortem corpus to retrieve from). Mark as v2 enhancement.
- Confidence calibration: 0.9 / 0.6 thresholds are heuristic; should be retuned against labeled data in W2.
