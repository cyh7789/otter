"""Otter — Governed LLM Runtime Change Control graph (v1).

This is the BPMN happy path the 5/30 hackathon submission targets:

    START
      └─ trigger_intake (IncidentTrigger in)
           └─ [parallel: metrics_agent + vendor_status_agent]
                └─ join → EvidenceBundle
                     └─ [conditional: proactive only]
                          ├─ eval_agent      → EvalBatchResult
                          └─ drift_detector  → DriftSignal
                     └─ diagnosis_agent      → DiagnosisOutput
                          └─ routing_decision_agent → RoutingProposal
                               └─ policy_gate       → PolicyDecision
                                    └─ canary_monitor → KillSwitchDecision
                                         └─ END

Every state hand-off is a Pydantic class from schemas.py. State is
strongly typed so a node that emits the wrong shape fails at the
boundary, not three nodes downstream (Codex high #4 prevention).

Every node honours `state["fixture_mode"]`. In fixture mode the node
returns deterministic mock data and never calls Gemini — this is the
demo / CI path that lets us record demo videos and run on flaky
network. In live mode the node would invoke an LLM; the v1 ship uses
stubs for the LLM calls themselves and a follow-up commit will wire
real `ChatGoogleGenerativeAI` invocations behind the same node
contracts (so the graph shape doesn't change when LLMs come online).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Optional

from typing_extensions import TypedDict
from langgraph.graph import StateGraph, START, END

from schemas import (
    DiagnosisOutput,
    DriftSignal,
    EvalBatchResult,
    EvidenceBundle,
    EvidencePacket,
    IncidentTrigger,
    KillSwitchDecision,
    PolicyDecision,
    RouteUtilityEstimate,
    RoutingProposal,
    SignalWeights,
)


# ---- shared state ---------------------------------------------------------

class State(TypedDict, total=False):
    """Typed lifecycle state. Every field is the corresponding Pydantic
    instance from schemas.py — nodes consume and produce typed contracts,
    not raw dicts."""

    trigger: IncidentTrigger
    metrics_packet: EvidencePacket
    vendor_packet: EvidencePacket
    evidence_bundle: EvidenceBundle
    eval_result: EvalBatchResult
    drift_signal: DriftSignal
    diagnosis: DiagnosisOutput
    routing_proposal: RoutingProposal
    policy_decision: PolicyDecision
    kill_switch: KillSwitchDecision

    fixture_mode: bool
    audit_log: Annotated[list[dict], lambda old, new: (old or []) + new]


def _audit(node: str, payload: dict) -> dict:
    """Helper: append a structured audit row from any node."""
    return {
        "audit_log": [
            {
                "node": node,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                **payload,
            }
        ]
    }


# ---- Block 1: trigger intake ---------------------------------------------

def trigger_intake(state: State) -> dict:
    """Sync inline. The trigger arrives in state["trigger"] (caller-set);
    this node just records the intake and is the natural place to add
    dedup / correlation logic later."""
    trigger = state["trigger"]
    return {
        **_audit("trigger_intake", {
            "incident_id": trigger.incident_id,
            "trigger_type": trigger.trigger_type,
            "source": trigger.source,
        }),
    }


# ---- Block 2: evidence agents (parallel) ---------------------------------

def metrics_agent(state: State) -> dict:
    """Collects live telemetry. Fixture mode returns a mock packet that
    simulates an elevated error rate (the proactive demo scenario)."""
    trigger = state["trigger"]
    now = datetime.now(timezone.utc)

    if state.get("fixture_mode"):
        payload = {"error_rate": 0.02, "baseline_error_rate": 0.005,
                   "window_minutes": 15}
        packet = EvidencePacket(
            agent_name="MetricsAgent", status="ok",
            data_completeness=1.0, criticality="critical",
            collected_at=now, payload=payload,
        )
    else:
        # TODO live: query metrics backend (Datadog / Prometheus / etc.).
        # For now degrade to a "no live integration yet" signal rather than
        # crashing — Codex high #3: vendor outage must not crash the entry.
        packet = EvidencePacket(
            agent_name="MetricsAgent", status="failed",
            data_completeness=0.0, criticality="critical",
            collected_at=now, payload={},
            errors=["live metrics integration not wired yet (v1 stub)"],
        )

    return {
        "metrics_packet": packet,
        **_audit("metrics_agent", {"status": packet.status,
                                    "completeness": packet.data_completeness}),
    }


def vendor_status_agent(state: State) -> dict:
    """Polls vendor status pages. Fixture returns 'all green' to demonstrate
    the silent-degradation scenario (metrics bad but vendor says ok)."""
    trigger = state["trigger"]
    now = datetime.now(timezone.utc)

    if state.get("fixture_mode"):
        payload = {"vendor": "google", "status": "operational",
                   "last_incident_minutes_ago": 240}
        packet = EvidencePacket(
            agent_name="VendorStatusAgent", status="ok",
            data_completeness=1.0, criticality="degradable",
            collected_at=now, payload=payload,
        )
    else:
        packet = EvidencePacket(
            agent_name="VendorStatusAgent", status="failed",
            data_completeness=0.0, criticality="degradable",
            collected_at=now, payload={},
            errors=["live vendor status integration not wired yet (v1 stub)"],
        )

    return {
        "vendor_packet": packet,
        **_audit("vendor_status_agent", {"status": packet.status}),
    }


def join_evidence(state: State) -> dict:
    """Joins the parallel packets into a single EvidenceBundle."""
    packets = [state["metrics_packet"], state["vendor_packet"]]
    completeness = sum(p.data_completeness for p in packets) / len(packets)
    bundle = EvidenceBundle(
        incident_id=state["trigger"].incident_id,
        packets=packets,
        overall_completeness=completeness,
        collected_at=datetime.now(timezone.utc),
    )
    return {
        "evidence_bundle": bundle,
        **_audit("join_evidence", {"packet_count": len(packets),
                                    "completeness": completeness}),
    }


# ---- Block 3: eval + drift (conditional on proactive) --------------------

def eval_agent(state: State) -> dict:
    trigger = state["trigger"]
    if state.get("fixture_mode"):
        result = EvalBatchResult(
            incident_id=trigger.incident_id, eval_status="ok",
            scores={"helpfulness": 0.74, "groundedness": 0.81},
            confidence=0.85,
            per_item_refs=["fixture-trace-1", "fixture-trace-2"],
            sample_count=50,
            baseline_ref="fixture-baseline-v1",
        )
    else:
        result = EvalBatchResult(
            incident_id=trigger.incident_id, eval_status="failed",
            scores={}, confidence=0.0, sample_count=0,
        )
    return {
        "eval_result": result,
        **_audit("eval_agent", {"status": result.eval_status,
                                 "confidence": result.confidence}),
    }


def drift_detector_agent(state: State) -> dict:
    trigger = state["trigger"]
    if state.get("fixture_mode"):
        signal = DriftSignal(
            incident_id=trigger.incident_id,
            drift_detected=True, severity_hint="HIGH",
            test_name="PSI", affected_rubrics=["helpfulness"],
            statistic=0.31, p_value=0.002,
        )
    else:
        signal = DriftSignal(
            incident_id=trigger.incident_id,
            drift_detected=False, severity_hint="LOW",
            test_name="PSI",
        )
    return {
        "drift_signal": signal,
        **_audit("drift_detector_agent", {"drift_detected": signal.drift_detected,
                                           "severity_hint": signal.severity_hint}),
    }


# ---- Block 4: diagnosis + routing ----------------------------------------

def diagnosis_agent(state: State) -> dict:
    trigger = state["trigger"]
    bundle = state["evidence_bundle"]
    drift = state.get("drift_signal")

    if state.get("fixture_mode"):
        # Mock the silent-degradation scenario: metrics bad, vendor green,
        # eval drift HIGH — diagnosis says vendor_silent_degradation.
        weights = SignalWeights(
            metrics_weight=0.55, logs_weight=0.0, dependency_weight=0.0,
            vendor_weight=0.10, eval_drift_weight=0.35,
        )
        diagnosis = DiagnosisOutput(
            incident_id=trigger.incident_id,
            root_cause="vendor_silent_degradation",
            root_cause_explanation=(
                "Metrics show error_rate 4x baseline (0.02 vs 0.005). "
                "Vendor status page reports all green but eval drift PSI "
                f"hit HIGH on rubric 'helpfulness' "
                f"(p={getattr(drift, 'p_value', 'n/a')}). Classic silent "
                "regression — vendor swapped the underlying checkpoint."
            ),
            incident_severity="HIGH",
            diagnosis_confidence=0.78,
            affected_rubrics=["helpfulness"],
            signal_summary=weights,
            conflicting_signals=["vendor status green vs metrics red"],
            evidence_bundle_ref=trigger.incident_id,
        )
    else:
        weights = SignalWeights(
            metrics_weight=0.2, logs_weight=0.2, dependency_weight=0.2,
            vendor_weight=0.2, eval_drift_weight=0.2,
        )
        diagnosis = DiagnosisOutput(
            incident_id=trigger.incident_id,
            root_cause="unknown",
            root_cause_explanation="live LLM diagnosis not wired yet (v1 stub)",
            incident_severity="LOW",
            diagnosis_confidence=0.0,
            signal_summary=weights,
        )

    return {
        "diagnosis": diagnosis,
        **_audit("diagnosis_agent", {"root_cause": diagnosis.root_cause,
                                      "severity": diagnosis.incident_severity,
                                      "confidence": diagnosis.diagnosis_confidence}),
    }


def routing_decision_agent(state: State) -> dict:
    trigger = state["trigger"]
    diagnosis = state["diagnosis"]

    if state.get("fixture_mode"):
        candidates = [
            RouteUtilityEstimate(
                model_id="gemini-2.5-pro", provider="google",
                quality_delta=0.12, cost_delta_usd=0.0008,
                confidence=0.72, pareto_rank=0,
                notes=["restores helpfulness rubric to baseline"],
            ),
            RouteUtilityEstimate(
                model_id="claude-sonnet-4.5", provider="anthropic",
                quality_delta=0.15, cost_delta_usd=0.0020,
                confidence=0.68, pareto_rank=1,
                notes=["higher quality, higher cost"],
            ),
        ]
        proposal = RoutingProposal(
            incident_id=trigger.incident_id,
            candidates=candidates, recommended=candidates[0],
            change_risk="MEDIUM", routing_confidence=0.72,
            temporary_route_ttl_minutes=60,
            route_type="quality_rescue", route_direction="forward",
            notes=["recommend Pareto-optimal candidate as temporary route"],
        )
    else:
        no_op = RouteUtilityEstimate(
            model_id="current", provider="unknown",
            quality_delta=0.0, cost_delta_usd=0.0,
            confidence=0.0, pareto_rank=0,
        )
        proposal = RoutingProposal(
            incident_id=trigger.incident_id,
            candidates=[no_op], recommended=no_op,
            change_risk="LOW", routing_confidence=0.0,
            route_type="quality_rescue", route_direction="forward",
            notes=["live routing decision not wired yet (v1 stub)"],
        )

    return {
        "routing_proposal": proposal,
        **_audit("routing_decision_agent", {
            "recommended_model": proposal.recommended.model_id,
            "change_risk": proposal.change_risk,
            "route_type": proposal.route_type,
        }),
    }


# ---- Block 4 end: policy gate (inline rules v1) --------------------------

def policy_gate(state: State) -> dict:
    """v1 inline rule executor — three rules. v2 will replace with a
    Git-backed DMN rule set so policy changes are reviewable."""
    trigger = state["trigger"]
    proposal = state["routing_proposal"]
    diagnosis = state["diagnosis"]

    if diagnosis.incident_severity == "CRITICAL":
        decision = PolicyDecision(
            incident_id=trigger.incident_id,
            decision="require_human",
            reason="severity=CRITICAL — every CRITICAL change requires human approval",
            rule_id="v1.severity-critical",
        )
    elif proposal.routing_confidence >= 0.6 and proposal.change_risk in ("LOW", "MEDIUM"):
        decision = PolicyDecision(
            incident_id=trigger.incident_id,
            decision="require_canary",
            reason=(
                f"routing_confidence={proposal.routing_confidence:.2f} >= 0.6 and "
                f"change_risk={proposal.change_risk} is MEDIUM-or-better — "
                "auto-allow gated behind canary monitoring."
            ),
            rule_id="v1.confident-medium-risk",
        )
    else:
        decision = PolicyDecision(
            incident_id=trigger.incident_id,
            decision="require_human",
            reason=(
                f"insufficient routing confidence ({proposal.routing_confidence:.2f}) "
                f"or change_risk={proposal.change_risk} too high for auto-allow."
            ),
            rule_id="v1.fallback-human",
        )

    return {
        "policy_decision": decision,
        **_audit("policy_gate", {"decision": decision.decision,
                                  "rule_id": decision.rule_id}),
    }


# ---- Block 5: canary monitor / kill switch -------------------------------

def canary_monitor(state: State) -> dict:
    """Compressed demo guard window. Fixture: 60s, no breach. v2 wires real
    metric polling."""
    trigger = state["trigger"]

    if state.get("fixture_mode"):
        decision = KillSwitchDecision(
            incident_id=trigger.incident_id,
            rollback=False,
            reason="all guard metrics within bounds across 60s window",
            metric_breached=None,
            observed_at=datetime.now(timezone.utc),
            canary_duration_seconds=60.0,
        )
    else:
        decision = KillSwitchDecision(
            incident_id=trigger.incident_id,
            rollback=False,
            reason="live canary monitoring not wired yet (v1 stub)",
            observed_at=datetime.now(timezone.utc),
            canary_duration_seconds=0.0,
        )

    return {
        "kill_switch": decision,
        **_audit("canary_monitor", {"rollback": decision.rollback,
                                     "reason": decision.reason[:80]}),
    }


# ---- routing helpers ------------------------------------------------------

def should_run_eval_drift(state: State) -> str:
    """Conditional edge after evidence join: only proactive incidents run
    the eval/drift pipeline. Reactive outages skip straight to diagnosis."""
    if state["trigger"].trigger_type == "proactive":
        return "eval_agent"
    return "diagnosis_agent"


# ---- builder --------------------------------------------------------------

def build_graph() -> Any:
    builder = StateGraph(State)

    builder.add_node("trigger_intake", trigger_intake)
    builder.add_node("metrics_agent", metrics_agent)
    builder.add_node("vendor_status_agent", vendor_status_agent)
    builder.add_node("join_evidence", join_evidence)
    builder.add_node("eval_agent", eval_agent)
    builder.add_node("drift_detector_agent", drift_detector_agent)
    builder.add_node("diagnosis_agent", diagnosis_agent)
    builder.add_node("routing_decision_agent", routing_decision_agent)
    builder.add_node("policy_gate", policy_gate)
    builder.add_node("canary_monitor", canary_monitor)

    builder.add_edge(START, "trigger_intake")

    # parallel fan-out: trigger_intake → both evidence agents
    builder.add_edge("trigger_intake", "metrics_agent")
    builder.add_edge("trigger_intake", "vendor_status_agent")

    # join: both evidence agents → join_evidence
    builder.add_edge("metrics_agent", "join_evidence")
    builder.add_edge("vendor_status_agent", "join_evidence")

    # conditional: proactive → eval/drift, reactive → straight to diagnosis
    builder.add_conditional_edges(
        "join_evidence",
        should_run_eval_drift,
        {"eval_agent": "eval_agent", "diagnosis_agent": "diagnosis_agent"},
    )
    builder.add_edge("eval_agent", "drift_detector_agent")
    builder.add_edge("drift_detector_agent", "diagnosis_agent")

    # linear tail: diagnosis → routing → policy → canary → END
    builder.add_edge("diagnosis_agent", "routing_decision_agent")
    builder.add_edge("routing_decision_agent", "policy_gate")
    builder.add_edge("policy_gate", "canary_monitor")
    builder.add_edge("canary_monitor", END)

    return builder.compile()


graph = build_graph()
