# Otter — Design Question Audit Trail

**Status as of 2026-05-19**: Round 1 (13 Qs) + Round 2 (5 follow-ups) + Round 3 (5 lifecycle / stability / identity questions) consulted with GPT-5 Pro. Decisions integrated into `../DESIGN.md` v4 + the per-domain sub-docs under `docs/`. This file is an audit trail only.

## Round 1 — Initial Flow Design (13 Qs)

| Q | Topic | Resolution → see |
|---|-------|------------------|
| Q1 | Unified vs split START | Two start events (message reactive + timer proactive) → shared Trigger Intake subprocess. `../DESIGN.md` §Top-Level BPMN |
| Q2 | All 4 investigation agents always? | `EvidencePlan.required_agents` dynamic per `trigger_type`, plus `optional_agents` degradable. `eval-drift-baseline.md` §Evidence collection schema |
| Q3 | Eval vs Drift order paradox | Cron drift sensing lives outside pipeline; in-pipeline EvalAgent emits fresh scores, DriftDetectorAgent runs statistical test. `eval-drift-baseline.md` |
| Q4 | HIGH/LOW decision authority | Diagnosis sets `incident_severity`; Routing sets `change_risk` + `routing_confidence`; PolicyGate decides human/canary/auto. `policy-gate.md` §Optimizer vs Governor |
| Q5 | Notification + PostMortem conditional? | Both async tail of Canary subprocess. Conditional on severity + customer tier. `canary-kill-switch.md` + `../DESIGN.md` §Block 5 |
| Q6 | CanaryAgent integration | Embedded subprocess in Block 5, triggered by `ApprovedRoutingDecision`. Daily baseline canary deferred to v2. `canary-kill-switch.md` |
| Q7 | Error handling | BPMN error boundary + per-agent `criticality: critical \| degradable`. `eval-drift-baseline.md` §EvidencePacket |
| Q8 | Action Center I/O schema | Full Pydantic schemas in `policy-gate.md` (`PolicyDecision`, `ApprovedRoutingDecision`). |
| Q9 | Concurrent incidents state | Maestro process instances isolated; shared resources (logs, eval baselines) live in Storage Buckets keyed by `correlation_key`. |
| Q10 | Flapping idempotency | Dedup window keyed by `correlation_key` in Trigger Intake. `lifecycle.md` §IncidentTrigger |
| Q11 | Parallel investigation timeout | `EvidencePlan.timeout_budget_seconds`; aggressive timeout + `EvidenceBundle.overall_completeness` carried into Diagnosis. |
| Q12 | Inter-agent schema boundaries | Pydantic at every block boundary; per-domain sub-docs own their types. |
| Q13 | Multi-tenancy | Single-tenant for v1 demo. Asset namespacing → `../DESIGN.md` §Remaining Open Questions Q1. |

## Round 2 — Architectural Reframe (2026-05-18 GPT-5 Pro)

| Topic | Decision | Where |
|-------|----------|-------|
| PolicyGate type | Business Rule Task (DMN), not LLM agent. Optional PolicyExplainerAgent narrates only. | `policy-gate.md` |
| 5-block subprocess sync/async | Blocks 1–4 sync; Block 5 sync guard window + async observation/notify/PM | `../DESIGN.md` §Top-Level BPMN |
| Kill switch dual-path | Normal rollback inside Canary subprocess; emergency global kill switch separate BPMN process | `canary-kill-switch.md` §Two rollback paths |
| Judge drift 4 signals | Gold agreement + inter-judge disagreement + position/format sensitivity + score distribution. Cascaded ensemble (primary/secondary/arbiter/human). | `eval-drift-baseline.md` §Judge Health |
| Cost-aware routing | 3-layer ceiling (incident/day/month) + `temporary_route_ttl_minutes`. Optimizer estimates, governor enforces. | `cost-aware-routing.md` |
| Baseline retrofit | Default no retrofit on judge upgrade — build `baseline_v2` via shadow scoring; freeze historical incidents on original baseline. | `eval-drift-baseline.md` §Judge versioning |
| Minimum demo metadata | Required fields per incident record for governance to be visible. | `../DESIGN.md` §Minimum Demo Governance Checklist |

## Round 3 — Lifecycle, Stability, Identity (2026-05-19 GPT-5 Pro)

| Topic | Priority | Decision | Where |
|-------|----------|----------|-------|
| Reverse routing — when to switch back to original model? | P0 | Incident lifecycle 7 states; RecoveryEvaluator separate from RoutingDecisionAgent; `temporary_route_ttl` triggers re-evaluation, not auto-switchback; restore uses same Canary subprocess with `route_direction=restore` | `lifecycle.md` |
| Canary failure loop prevention | P0 | RouteCircuitBreaker (deterministic state machine) before RoutingDecisionAgent; 3-layer retry budget; CONTAINMENT_MODE when rollback target also unhealthy | `canary-kill-switch.md` §RouteCircuitBreaker + §CONTAINMENT_MODE |
| Silent model upgrade detection | P1 (demo killer) | ModelIdentityMonitor 4-tier identity (requested / declared / provider fingerprint / behavioral); 5 probe types; claim discipline ("detect suspected", not "prove swap") | `model-identity.md` |
| DMN authoring + Git versioning | P1 (W2 must) | Git is source of truth; Orchestrator stores release artifacts; `policy_identity = name + semver + git_sha + dmn_checksum + orchestrator_BR_version`; manifest.yaml mandatory | `policy-gate.md` §Git-backed policy versioning |
| Eval baseline cold start | P1/P2 | BaselineProfile maturity L0–L4; PolicyGate gates proactive auto-route by readiness; v1 simplification = L0/L3 binary; 5 bootstrap paths | `eval-drift-baseline.md` §Baseline Maturity |

### Round 3 architectural impact

- New LLM agents: **RecoveryEvaluator** (#9), **ModelIdentityMonitor** (#10)
- New deterministic services: **RouteCircuitBreaker**, **BaselineProfile gate**
- Repositioned tagline: **"Governed LLM Runtime Change Control"** — supersedes pure auto-routing framing
- ~15 new schemas across the sub-docs

## Original Questions (preserved for context)

---

## Context

Otter is a UiPath AgentHack 2026 Track 2 (Maestro BPMN) submission.

**One-line concept**: Continuous LLM Agent Evaluation & Auto-Routing — protect product quality when (a) LLM vendor breaks, or (b) model silently degrades.

**Stack**: LangGraph (Python) inside UiPath Cloud (Maestro BPMN + Action Center + Assets + Buckets + Queues + Context Grounding).

**Trigger sources**:
- **Reactive** — incident webhook (vendor outage / latency spike / error rate threshold)
- **Proactive** — scheduled eval (hourly/daily LLM-as-judge on production sample)

**9 agent roles** (current rough sketch, see `../DESIGN.md`):
1. LogAnalyzerAgent — application logs
2. VendorStatusAgent — LLM provider status pages
3. MetricsAgent — Datadog / Prometheus (latency / cost / error rate)
4. DependencyAgent — upstream service health
5. EvalAgent — LLM-as-judge multi-rubric
6. DriftDetectorAgent — statistical drift (KS / CUSUM / Bayesian)
7. DiagnosisAgent — synthesize all signals → root cause + severity
8. RoutingDecisionAgent — proposed model switch
9. NotificationAgent — on-call + customer SLA alerts
10. PostMortemAgent — draft incident report

(Plus CanaryAgent on a separate scheduled flow — see Q6.)

**Architecture sketch** (from DESIGN.md):
```
START → [parallel 4 investigation] → [join] → Eval → Drift → Diagnosis → [HIGH/LOW exclusive] → Notification → PostMortem → END
```

---

## Q1 — START event: 1 unified or 2 separate?

**Problem**: Reactive (incident webhook) and Proactive (scheduled cron) are very different triggers. BPMN 2.0 supports multiple start events in one process (different trigger types feeding shared downstream).

**Options**:
- **A. Unified 1-process**: start event acts as dispatcher; first task node branches on `trigger_type` field. Downstream reused.
- **B. Two separate processes**: cleaner BPMN diagram, different trigger event types (message vs timer). Downstream agents duplicated or extracted to sub-process.
- **C. 1 process with 2 start events** (BPMN multi-start pattern): if Maestro supports it, cleanest.

**Need from you**: Which option? Does Maestro support BPMN multi-start? If A, where exactly to put the dispatcher?

---

## Q2 — Do all 4 investigation agents run for both triggers?

**Reactive case** (vendor down): LogAnalyzer + VendorStatus + Metrics + Dependency are all clearly needed.

**Proactive case** (drift detected, vendor is up): VendorStatus probably not needed; Dependency probably not needed; LogAnalyzer + Metrics still useful.

**Options**:
- **A. Always run all 4** (simple, possibly wasteful)
- **B. Trigger-aware subset** (Reactive runs 4, Proactive runs 2) — adds gateway complexity
- **C. Adaptive** — Diagnosis decides if it needs more data, can spawn missed agents (most flexible, most complex)

**Need from you**: Recommendation + how to express in BPMN clearly.

---

## Q3 — Eval vs Drift ordering paradox

**Conflict**: DESIGN diagram shows Eval → Drift (Drift is a downstream detector). But Proactive trigger is itself "drift was detected, now we run the pipeline" — the order contradicts itself.

**Hypothesis**: separate two roles —
- **Routine drift monitoring** (cron job, lightweight) = trigger source, lives outside main pipeline
- **In-pipeline EvalAgent** = deeper LLM-as-judge eval on incident-related sample
- **In-pipeline DriftDetectorAgent** = statistical comparison against historical baseline using fresh eval scores

So pipeline order is: EvalAgent (fresh judging) → DriftDetectorAgent (statistical test) → DiagnosisAgent. Routine drift is upstream of the whole process.

**Need from you**: Validate or propose better separation. Naming suggestions welcome.

---

## Q4 — HIGH/LOW severity: which agent decides?

**Options**:
- **A. DiagnosisAgent decides** — severity is a property of root cause + impact
- **B. RoutingDecisionAgent decides** — severity is about routing risk (how disruptive is the switch)
- **C. Both** — Diagnosis sets `incident_severity`; Routing sets `change_risk`; combined gate uses both

**Hypothesis**: C, but cleanly split: `incident_severity` (HIGH/LOW) gates whether to involve a human; `change_risk` gates whether to do canary first.

**Need from you**: Confirm or refine. How should the gateway condition expression look?

---

## Q5 — Notification + PostMortem: always run or conditional?

**Problem**: If LOW severity (auto-routed quietly), do we still page on-call? Do we still generate a full post-mortem?

**Options**:
- **A. Always run both** — simple, may cause alert fatigue
- **B. Conditional** — LOW: Slack only + auto-archive post-mortem; HIGH: PagerDuty + human-reviewed post-mortem
- **C. Customer-configurable thresholds** stored in UiPath Assets

**Need from you**: B + which thresholds are customer-configurable? Production best practice?

---

## Q6 — CanaryAgent integration

**Problem**: DESIGN says "parallel scheduled flow" but doesn't specify the relationship.

**Options**:
- **A. Independent BPMN process**, manually triggered on model introduction
- **B. Triggered by RoutingDecisionAgent** — once routing decided, spawn canary sub-process for progressive traffic shift
- **C. Both** — daily baseline canary cron (A) + post-routing canary (B)

**Hypothesis**: B is primary; A is optional cron for baseline drift sensing.

**Need from you**: Recommendation. Should canary live in same process as a parallel branch, or be a separate process invoked via message?

---

## Q7 — Error handling: agent failure path

**Problem**: What if LogAnalyzerAgent itself crashes (API timeout, malformed response, OOM)?

**Options**:
- **A. Whole process fails** (conservative, safe)
- **B. BPMN error boundary event** + graceful degradation — other 3 investigation agents continue; Diagnosis marks "partial data"
- **C. Retry policy** with exponential backoff + circuit breaker

**Hypothesis**: B + A hybrid. Some agents are nice-to-have (Dependency, LogAnalyzer) → degrade; some are critical (VendorStatus, Diagnosis) → fail process.

**Need from you**: Which agents are critical vs degradable? BPMN error pattern in Maestro?

---

## Q8 — Action Center gate I/O schema

Two human gates per DESIGN:

**Gate 1: HIGH severity routing approval**
- Show human: diagnosis summary, proposed routing change (from-model → to-model), estimated impact (latency / cost / quality delta), prior similar incidents
- Human returns: approve / reject / modify (override target model)

**Gate 2: PostMortem review**
- Show human: AI-drafted post-mortem + all raw incident data (eval scores, drift signal, agent outputs)
- Human returns: approve as-is / edit / require more data (loops back)

**Need from you**: Schema sufficient? Anything critical missing for either gate? UX flow improvements?

---

## Q9 — State management for concurrent incidents

**Problem**: Two vendors break simultaneously → two process instances run in parallel. State isolation between instances?

**Assumption**: Each Maestro process instance has isolated state. Shared resources (incident queue, log bucket, eval baseline data) need careful read/write patterns.

**Need from you**: Recommended UiPath patterns for shared-state safety? Any gotchas?

---

## Q10 — Idempotency on flapping triggers

**Problem**: Vendor status page flaps — webhook fires 3 times in 60s for same vendor. Start 3 processes? Or dedupe?

**Options**:
- **A. Always new process** (simple, may cause duplicate routing decisions)
- **B. Dedup window** — 5 min, same vendor → ignore subsequent
- **C. Attach pattern** — subsequent events feed data into the first process via correlation key

**Hypothesis**: B for v1 demo, evolve to C if time.

**Need from you**: Recommendation + how to implement in Maestro (correlation keys? message events?).

---

## Q11 — Timeout strategy for parallel investigation agents

**Problem**: 4 parallel agents, join waits for slowest. LogAnalysis on 1 GB log could take 30s+.

**Options**:
- **A. Fixed timeout per agent** (e.g., 20s) — drop slow agents
- **B. Aggressive timeout + partial data** — Diagnosis marks `data_completeness: 0.75`
- **C. Adaptive** — first time longer, retries shorter

**Hypothesis**: B for incident response (speed matters more than completeness). HIGH-severity human gate gives human chance to wait for more data if needed.

**Need from you**: Validate. Sensible default timeout?

---

## Q12 — Inter-agent output schema

**Problem**: LangChain messages vs Pydantic structured outputs.

**Options**:
- **A. Messages all the way** — flexible, easy for LLM agents, downstream parses
- **B. Structured Pydantic everywhere** — type-safe, easy for UiPath BPMN, more boilerplate
- **C. Mixed** — internal LangGraph state uses messages; cross-boundary contracts (Diagnosis → Routing, Eval → Drift) use Pydantic models

**Hypothesis**: C, but where exactly are the boundaries?

**Need from you**: Define schema boundaries. Suggest Pydantic models for the critical handoffs.

---

## Q13 — Multi-tenancy

**Problem**: Is Otter SaaS-for-many-customers, or single-tenant internal tool?

**Implications**:
- Eval rubric: global vs per-customer
- Routing policy: per-customer (which models they're allowed to switch to, cost ceilings)
- UiPath Assets: how to namespace per tenant
- Quota / cost isolation

**Hypothesis for hackathon demo**: Single-tenant. DESIGN acknowledges multi-tenant as v2 roadmap. Don't over-engineer for demo.

**Need from you**: Agree to defer? Or is there a lightweight multi-tenant pattern worth including now (e.g., tenant prefix on all UiPath Asset keys)?

---

## Reply format

For each question:
- **Recommendation**: which option (or new option)
- **Reasoning**: 2-3 sentences why
- **Trade-off**: what we give up
- **UiPath/BPMN specifics** (where applicable): how to express in Maestro

Bonus: any open issues not in this list that you'd flag for a Track 2 submission.
