# Eval, Drift Detection, Judge Health & Baseline Maturity

> Sub-doc of `../DESIGN.md`. Owns: eval methodology, drift taxonomy, cascaded judge ensemble, JudgeHealthSignal, BaselineProfile maturity gate.

## Eval signals

Combine four signal sources to avoid single-signal false alarms:

- **LLM-as-judge multi-rubric** — accuracy, relevance, hallucination, safety, coherence, tone, format. Weighted aggregate per `rubric_version`.
- **Production sample** — 5–10%, focused on critical paths. Bucket-backed sample store; refs only in process state.
- **Reference-based faithfulness** — RAG context only. Compare response against retrieved sources.
- **Cost / performance** — latency p99, cost per request, error rate.

## Cascaded judge ensemble

Not flat voting. Disagreement-driven escalation keeps cost down while maintaining human agreement guarantees.

| Tier | Model | Frequency |
|------|-------|-----------|
| Primary | Cheap, stable judge | Every sampled call |
| Secondary | Different vendor judge | Random subsample |
| Arbiter | Expensive judge | Only on disagreement OR high severity |
| Human calibration | Golden set, ~50–200 anchors | Daily smoke + quarterly review |

This is **selective evaluation**, not "three judges vote so it's truth". A judge ensemble's agreement signal is not the same as ground truth — see Judge Health below.

## Drift taxonomy

Four shapes of drift, four matched detectors:

| Drift type | Signal | Default threshold (v1) |
|------------|--------|------------------------|
| Performance | Eval rolling 24h avg | drop > 5% vs baseline |
| Concept | KS test on score distribution | p < 0.05 |
| Slow drift | CUSUM | configured break |
| Latency | p99 | > 2x baseline |
| Error rate | sustained | > 1% for > 5 min |
| Hallucination | judge-detected | > 3% |
| Cost | per response | > 2x baseline |

Composite signal (weighted) gates the routing pipeline; single-signal alerts only when severity ≥ HIGH.

## Judge Health — separate from production drift

**Cross-judge disagreement is not a judge-drift detector.** It tells you judges disagree, not who is right. Four independent signals must be computed:

1. **Gold agreement drift** — judge vs human-labeled golden set. Drop triggers recalibration.
2. **Inter-judge disagreement** — cross-vendor ensemble disagreement. Spike triggers human audit; **does not** label the production model as degraded.
3. **Position / format sensitivity** — swap pair order or reformat input; score variance > threshold → judge bias warning.
4. **Score distribution drift** — anchor-set mean / variance / per-rubric shift over time.

`JudgeCalibrationReport.action ∈ {use, use_with_human_escalation, shadow_only, disable}` flows into `EvalDriftReport.judge_health.healthy_for_routing_decision`. If unhealthy:

- RoutingDecisionAgent must downgrade `routing_confidence`.
- PolicyGate then likely returns `require_human` via rule 8.

## Judge versioning

Every evaluation pins:

```
judge_model, judge_model_version, prompt_version, rubric_version, decoding_config_hash
```

**Baseline retrofit policy**: default **do not retrofit** when judge upgrades. Build `baseline_v2` via shadow scoring:

1. Run new judge on anchor set.
2. Run new judge on last N days production sample.
3. Build `v1 → v2` score mapping.
4. Promote `baseline_v2` if mapping is stable.
5. Historical incidents stay frozen on `baseline_v1`.

Cross-version trend dashboards may use `backfilled_baseline_v2`, but it must not overwrite incident audit baselines.

## Baseline Maturity — cold start gate

Fresh tenants have no production history. Letting DriftDetector pretend it has statistical confidence is an auto-routing safety hazard. Each tenant carries a maturity level:

| Maturity | Conditions | Allowed actions |
|----------|------------|-----------------|
| L0 No baseline | No tenant data, no golden set | Reactive outage routing only; no quality-drift auto-route |
| L1 Global bootstrap | 50–100 generic golden / synthetic eval | Warning only; auto-route disabled |
| L2 Shadow baseline | 3–7 days production samples, 200+ per major segment, judge health pass | LOW/MEDIUM conservative canary |
| L3 Tenant stable | 14+ days, 500+ per critical rubric, variance stable | Full auto-route policy |
| L4 Audited | Human-labeled golden set + historical incidents | Enterprise / regulated tenant |

**v1 demo simplification**: implement L0/L3 binary only. Full L1–L4 is v2.

## Cold-start bootstrap paths

Five compatible approaches, used in combination:

1. **Global baseline** — generic probe / rubric. Smoke test only; not tenant-specific truth.
2. **Synthetic baseline** — generate tenant-representative tasks. Requires human or strong-judge calibration to avoid bias.
3. **Shadow production baseline** — onboard customer, observe outputs, do not auto-route. Most reliable v1 path.
4. **Paired model baseline** — same sample run through current + fallback model simultaneously. Use pairwise preference / delta instead of absolute scores.
5. **Human seed labels** — for enterprise / regulated tenants, require 50–200 human-labeled gold examples up front.

## PolicyGate integration

PolicyGate reads BaselineReadinessDecision (`policy-gate.md` rule 17):

```
if trigger_type == proactive_quality
   and baseline.sufficient_for_drift_detection == False:
    deny auto_route
    action = warning_only OR human_review
```

Reactive outage routing is not gated by baseline maturity — vendor going down doesn't need quality baseline to detect.

## Schema

### Evidence collection (Block 2)

```python
from typing import Literal
from pydantic import BaseModel, Field


class EvidencePacket(BaseModel):
    incident_id: str
    agent_name: str
    status: Literal["ok", "timeout", "failed", "skipped"]
    criticality: Literal["critical", "degradable"]
    summary: str | None
    confidence: float = Field(ge=0, le=1)
    data_completeness: float = Field(ge=0, le=1)
    metrics: dict = {}
    evidence_refs: list[str] = []
    missing_reason: str | None = None


class EvidenceBundle(BaseModel):
    incident_id: str
    packets: list[EvidencePacket]
    overall_completeness: float = Field(ge=0, le=1)
    critical_missing: list[str]
    degradable_missing: list[str]
```

### Eval + drift + judge health (Block 3)

```python
class EvalBatchResult(BaseModel):
    incident_id: str
    judge_model: str
    judge_model_version: str
    rubric_version: str
    baseline_version: str
    sample_count: int
    scores: dict[str, float]
    confidence: float = Field(ge=0, le=1)
    per_item_refs: list[str]


class DriftSignal(BaseModel):
    incident_id: str
    drift_detected: bool
    test_name: str
    statistic: float
    p_value: float | None
    posterior_probability: float | None
    affected_rubrics: list[str]
    severity_hint: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    baseline_version: str


class JudgeRunIdentity(BaseModel):
    judge_model: str
    judge_vendor: str
    judge_model_version: str
    prompt_version: str
    rubric_version: str
    decoding_config_hash: str


class JudgeCalibrationReport(BaseModel):
    judge_identity: JudgeRunIdentity
    calibration_set_version: str
    anchor_set_version: str
    evaluated_at: str

    gold_agreement_rate: float | None
    gold_agreement_delta: float | None
    inter_judge_disagreement_rate: float | None
    pairwise_agreement: dict[str, float] = {}
    position_bias_score: float | None
    format_sensitivity_score: float | None
    score_distribution_shift: float | None

    healthy: bool
    action: Literal["use", "use_with_human_escalation", "shadow_only", "disable"]
    reasons: list[str]


class JudgeHealthSignal(BaseModel):
    judge_model: str
    judge_model_version: str
    rubric_version: str
    calibration_set_version: str
    inter_judge_disagreement: float | None
    gold_agreement: float | None
    position_bias_score: float | None
    self_preference_risk: Literal["low", "medium", "high", "unknown"]
    healthy_for_routing_decision: bool


class EvalDriftReport(BaseModel):
    incident_id: str
    eval_status: Literal["ok", "timeout", "failed", "skipped"]
    eval_result: EvalBatchResult | None
    drift_signal: DriftSignal | None
    judge_health: JudgeHealthSignal | None


class BaselineProfile(BaseModel):
    tenant_id: str
    model_id: str
    rubric_version: str
    judge_model: str
    judge_model_version: str

    baseline_version: str
    maturity: Literal["L0", "L1", "L2", "L3", "L4"]

    sample_count: int
    observation_days: int
    segment_coverage: dict[str, int]
    created_at: str
    last_refreshed_at: str

    quality_mean: dict[str, float] = {}
    quality_std: dict[str, float] = {}
    min_detectable_effect: float | None = None

    allowed_actions: list[Literal[
        "record_only",
        "warning_only",
        "human_review",
        "auto_canary",
        "auto_route",
    ]]


class BaselineReadinessDecision(BaseModel):
    incident_id: str
    baseline_version: str | None
    maturity: Literal["L0", "L1", "L2", "L3", "L4"]
    sufficient_for_drift_detection: bool
    sufficient_for_auto_route: bool
    reason: str
    required_next_step: Literal[
        "collect_shadow_data",
        "run_synthetic_eval",
        "request_human_labels",
        "ok",
    ]
```

## Trade-off

Fresh tenants look conservative — Otter cannot demo proactive auto-routing on day one without a baseline. This is correct behavior. The demo uses an L3 tenant, and design doc explicitly notes cold-start gating for new tenants.

## Cross-refs

- Gated by: `policy-gate.md` (rule 17)
- Recovery uses baseline for quality recovery check: `lifecycle.md` (Condition 2)
- Judge identity overlap with model identity probe set: `model-identity.md`
