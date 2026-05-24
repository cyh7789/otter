"""Otter — typed contracts between agents in the Governed LLM Runtime Change Control loop.

Single Pydantic module so every node in graph.py, every UiPath entry point,
every fixture, and every test asserts against the same shape. The agent specs
in `agents/*.md` are the design-time source; this file is the runtime source.

v1 schemas (10) — minimum to ship the 5/30 happy path:
    IncidentTrigger
    EvidencePacket
    EvidenceBundle
    EvalBatchResult
    DriftSignal
    DiagnosisOutput
    RouteUtilityEstimate
    RoutingProposal
    PolicyDecision
    KillSwitchDecision

v2+ deferred (5/31+):
    RubricDefinition, BaselineProfile, JudgeRunIdentity, JudgeHealthSignal,
    ModelCapability, TenantConstraint, CostBudgetSnapshot,
    CircuitBreakerState, CompleteLifecycleState.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# ---- shared types ----------------------------------------------------------

Severity = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
TriggerType = Literal["reactive", "proactive"]
AgentStatus = Literal["ok", "failed", "timeout"]
EvidenceCriticality = Literal["critical", "degradable"]


class _Base(BaseModel):
    """Project-wide Pydantic defaults. Strict on extra fields so a typo in
    an agent's JSON output fails fast rather than silently dropping data."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ---- Block 1: incident trigger --------------------------------------------

class IncidentTrigger(_Base):
    """Entry point for the graph. Produced by TriggerIntake from either a
    reactive alert (paging system) or a proactive eval/drift signal."""

    incident_id: str
    trigger_type: TriggerType
    correlation_key: str = Field(
        ..., description="Stable key for dedup across the lifecycle."
    )
    timestamp: datetime
    source: str = Field(
        default="manual",
        description="e.g. 'pagerduty', 'eval_scheduler', 'manual', 'fixture'.",
    )
    raw_signal: Optional[dict] = Field(
        default=None,
        description="Original payload (alert body / drift detector output).",
    )


# ---- Block 2: evidence bundle ---------------------------------------------

class EvidencePacket(_Base):
    """One agent's contribution to the evidence bundle. Status + criticality
    let DiagnosisAgent decide whether a packet is trustworthy and whether a
    failed packet is fatal."""

    agent_name: str
    status: AgentStatus
    data_completeness: float = Field(ge=0.0, le=1.0)
    criticality: EvidenceCriticality
    collected_at: datetime
    payload: dict = Field(
        default_factory=dict,
        description="Agent-specific structured output (e.g. metrics window, "
                    "vendor status JSON, log slice).",
    )
    errors: list[str] = Field(default_factory=list)


class EvidenceBundle(_Base):
    """Parallel-collected packets joined for Block 3 / Block 4 consumption."""

    incident_id: str
    packets: list[EvidencePacket]
    overall_completeness: float = Field(ge=0.0, le=1.0)
    collected_at: datetime
    notes: list[str] = Field(default_factory=list)


# ---- Block 3: eval + drift ------------------------------------------------

EvalStatus = Literal["ok", "timeout", "failed"]


class EvalBatchResult(_Base):
    """EvalAgent output. Per-rubric scoring on a held-out batch."""

    incident_id: str
    eval_status: EvalStatus
    scores: dict[str, float] = Field(
        default_factory=dict,
        description="rubric_name -> mean score in [0, 1].",
    )
    confidence: float = Field(ge=0.0, le=1.0)
    per_item_refs: list[str] = Field(
        default_factory=list,
        description="Trace / sample IDs the scores were computed over.",
    )
    sample_count: int = Field(ge=0)
    baseline_ref: Optional[str] = Field(
        default=None,
        description="ID of the BaselineProfile this batch was compared against.",
    )


class DriftSignal(_Base):
    """DriftDetectorAgent output."""

    incident_id: str
    drift_detected: bool
    severity_hint: Severity
    test_name: str = Field(
        ..., description="Drift test that triggered (e.g. KS, PSI, custom)."
    )
    affected_rubrics: list[str] = Field(default_factory=list)
    statistic: Optional[float] = None
    p_value: Optional[float] = None


# ---- Block 4: diagnosis + routing -----------------------------------------

RootCauseCategory = Literal[
    "vendor_outage_confirmed",
    "vendor_silent_degradation",
    "dependency_degradation",
    "model_quality_drift",
    "capacity_pressure",
    "schema_break",
    "auth_failure",
    "cost_spike",
    "unknown",
]


class SignalWeights(_Base):
    metrics_weight: float = Field(ge=0.0, le=1.0)
    logs_weight: float = Field(ge=0.0, le=1.0)
    dependency_weight: float = Field(ge=0.0, le=1.0)
    vendor_weight: float = Field(ge=0.0, le=1.0)
    eval_drift_weight: float = Field(ge=0.0, le=1.0)


class DiagnosisOutput(_Base):
    """DiagnosisAgent's structured hypothesis. RoutingDecisionAgent consumes
    this — DiagnosisAgent never proposes a remediation itself."""

    incident_id: str
    root_cause: RootCauseCategory
    root_cause_explanation: str = Field(max_length=400)
    incident_severity: Severity
    diagnosis_confidence: float = Field(ge=0.0, le=1.0)
    affected_rubrics: list[str] = Field(default_factory=list)
    signal_summary: SignalWeights
    conflicting_signals: list[str] = Field(default_factory=list)
    recommended_evidence_focus: Optional[list[str]] = None
    evidence_bundle_ref: Optional[str] = None
    eval_drift_report_ref: Optional[str] = None


RouteType = Literal["quality_rescue", "cost_saving", "latency_rescue"]
RouteDirection = Literal["forward", "restore"]


class RouteUtilityEstimate(_Base):
    """One candidate model/route option scored on the multi-objective frontier."""

    model_id: str
    provider: str = Field(default="", description="e.g. anthropic, openai, google.")
    quality_delta: float = Field(
        description="Estimated quality change vs current route (positive = better)."
    )
    cost_delta_usd: float = Field(
        description="Estimated $ per 1K requests change vs current route."
    )
    latency_delta_ms: float = Field(default=0.0)
    confidence: float = Field(ge=0.0, le=1.0)
    pareto_rank: int = Field(ge=0)
    notes: list[str] = Field(default_factory=list)


class RoutingProposal(_Base):
    """RoutingDecisionAgent output. PolicyGate validates against constraints
    before any route swap is allowed to take effect."""

    incident_id: str
    candidates: list[RouteUtilityEstimate]
    recommended: RouteUtilityEstimate
    change_risk: Severity
    routing_confidence: float = Field(ge=0.0, le=1.0)
    temporary_route_ttl_minutes: Optional[int] = Field(default=None, ge=0)
    route_type: RouteType
    route_direction: RouteDirection
    notes: list[str] = Field(default_factory=list)


# ---- Block 4 end: policy gate ---------------------------------------------

PolicyDecisionType = Literal[
    "allow_auto",
    "require_canary",
    "require_human",
    "deny",
]


class PolicyDecision(_Base):
    """PolicyGate output. Auditable per-decision record — `reason` must
    cite which policy rule fired."""

    incident_id: str
    decision: PolicyDecisionType
    reason: str
    policy_version: str = Field(
        default="v1-inline",
        description="Identifier for the rule set evaluated (v1 = inline,"
                    " v2+ = Git-backed DMN).",
    )
    rule_id: Optional[str] = Field(
        default=None,
        description="Specific rule that produced the decision, if applicable.",
    )


# ---- Block 5: canary monitor / kill switch --------------------------------

class KillSwitchDecision(_Base):
    """CanaryMonitor's verdict at the end of the guard window."""

    incident_id: str
    rollback: bool
    reason: str
    metric_breached: Optional[str] = Field(
        default=None,
        description="Name of the metric that crossed the kill-switch threshold,"
                    " or null if no breach.",
    )
    observed_at: datetime
    canary_duration_seconds: float = Field(ge=0.0)


# ---- exported (v1) --------------------------------------------------------

__all__ = [
    "Severity",
    "TriggerType",
    "AgentStatus",
    "EvidenceCriticality",
    "EvalStatus",
    "RootCauseCategory",
    "RouteType",
    "RouteDirection",
    "PolicyDecisionType",
    "SignalWeights",
    "IncidentTrigger",
    "EvidencePacket",
    "EvidenceBundle",
    "EvalBatchResult",
    "DriftSignal",
    "DiagnosisOutput",
    "RouteUtilityEstimate",
    "RoutingProposal",
    "PolicyDecision",
    "KillSwitchDecision",
]
