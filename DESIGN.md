# Otter Design Document

**UiPath AgentHack 2026 — Track 2 (Maestro BPMN)**

> v4 — 2026-05-19. Detail split into per-domain sub-docs under `docs/`. This file is the index + architectural decisions + component inventory + top-level BPMN. For domain detail and full schemas, follow the links.

## Problem Statement

Enterprises adopting LLM-based agents face two silent failure modes:

1. **Vendor outages** — OpenAI / Anthropic / Google API failures. Status pages lag behind. Fallback logic is scattered across services.
2. **Silent quality regression** — Underlying model updates change behavior. Hallucination rates creep up. Tone drifts. "The model is technically responding" but quality has degraded — sometimes because the vendor silently swapped the underlying checkpoint.

Existing tooling addresses only the first (LiteLLM, Portkey, OpenRouter) via manual routing rules. No standard solution exists for **continuous evaluation, deterministic governance, and audited automatic re-routing — with a return-to-normal path**.

## Core Concept

**Governed LLM Runtime Change Control.**

Two protection triggers, one orchestration, one audit trail:

| Trigger | When | Response |
|---------|------|----------|
| **Reactive** | Vendor outage / timeout / error rate spike | Fallback to backup model |
| **Proactive** | Eval score drift / hallucination uptick / tone change / silent identity shift | Auto-route to higher-quality model |

User promise: **"Whether the vendor breaks, the model silently degrades, or the vendor swaps the model underneath you — your quality stays protected, and every change is auditable."**

## Architectural Decision: Optimizer vs Governor

Routing has two responsibilities that **must not** be conflated:

- **Optimizer (LLM agent)** — propose candidate routes, rank by quality / latency / cost trade-off, estimate blast radius, suggest canary plan.
- **Governor (deterministic rule engine)** — decide whether a proposed route is allowed under tenant policy, cost cap, data residency, PII handling, rollback availability, SLA, baseline maturity, and circuit-breaker state.

LLM reasoning is acceptable for proposing; it is **not** acceptable for the final allow/deny. Governance decisions must be reproducible, version-pinned, and auditable.

Otter implements three deterministic services that LLM agents are forbidden from owning:

- **PolicyGate** (Maestro Business Rule Task, DMN 1.3) — authorization
- **RouteCircuitBreaker** — retry budget, cooldown, loop prevention
- **BaselineProfile gate** — cold-start safety

See `docs/policy-gate.md`, `docs/canary-kill-switch.md`, `docs/eval-drift-baseline.md`.

## Component Inventory

**10 LLM agents + 3 deterministic services + 1 optional explainer.**

| # | Component | Type | Owns | Detail |
|---|-----------|------|------|--------|
| 1 | LogAnalyzerAgent | LLM | Application logs | Block 2 |
| 2 | VendorStatusAgent | LLM | LLM provider status pages | Block 2 |
| 3 | MetricsAgent | LLM | Datadog / Prometheus | Block 2 |
| 4 | DependencyAgent | LLM | Upstream service health | Block 2 |
| 5 | EvalAgent | LLM | LLM-as-judge multi-rubric | `eval-drift-baseline.md` |
| 6 | DriftDetectorAgent | LLM | Statistical drift + judge health | `eval-drift-baseline.md` |
| 7 | DiagnosisAgent | LLM | Synthesize signals → root cause + severity | Block 4 |
| 8 | RoutingDecisionAgent | LLM | Propose outbound candidates + cost estimate | `cost-aware-routing.md` |
| 9 | **RecoveryEvaluator** | LLM | Propose restore candidates | `lifecycle.md` |
| 10 | **ModelIdentityMonitor** | LLM (probe runner) | Detect silent model upgrades | `model-identity.md` |
| — | **PolicyGate** | DMN Business Rule Task | Allow / deny / require-canary / require-human | `policy-gate.md` |
| — | **RouteCircuitBreaker** | Deterministic state machine | Retry budget, circuit state | `canary-kill-switch.md` |
| — | **BaselineProfile gate** | Deterministic check | Cold-start maturity | `eval-drift-baseline.md` |
| — | PolicyExplainerAgent *(optional)* | LLM | Narrate PolicyGate decision (no authority) | `policy-gate.md` |

Closure-path support: NotificationAgent + PostMortemAgent run async after Block 5.

## Top-Level BPMN — 5 Block Subprocess Map

```
[Start: Reactive Message | Proactive Timer]
    ↓
(1) Trigger Intake          sync inline subprocess
    ↓
[Parallel gateway]
    ├→ (2) Evidence Collection      sync call activity (parallel multi-instance inside)
    └→ (3) Eval + Drift             sync call activity (timeout, depth-configurable)
[Join]
    ↓
(4) Decision + Policy Gate  sync call activity
    ↓
(5) Canary + Monitor        sync guard window → async observation + notify + postmortem
    ↓
[End]
```

Loop guard (RouteCircuitBreaker) sits at the top of Block 4. Identity probes (ModelIdentityMonitor) run on independent timer + inline checks at Block 4 and the lifecycle's RESTORE_CANARY entry.

| Block | Mode | Why |
|-------|------|-----|
| 1 Trigger Intake | sync | Normalized trigger + dedupe + evidence plan before fan-out |
| 2 Evidence Collection | sync, parallel multi-instance | Diagnosis cannot run on empty evidence |
| 3 Eval + Drift | sync with timeout; deep eval may continue async | Routing needs at least shallow eval/drift signal |
| 4 Decision + Gate | sync (Human Task waits when required) | Approval is a flow-critical join |
| 5 Canary + Monitor | guard window sync; observation + notify + PM async | Incident must not close before guard window passes |

Block 5 also owns **return-to-normal** via the same Canary subprocess with `route_direction = "restore"`. See `lifecycle.md` for the 7-state incident machine.

## Differentiation vs Commercial Tools

| Tool | What It Does | What It Lacks |
|------|--------------|---------------|
| LiteLLM / Portkey | Manual routing rules | No continuous eval, no governance layer, no return-to-normal |
| Helicone | Metrics collection | No automatic routing |
| LangSmith / Langfuse | Tracing + eval | No auto-routing, no orchestrated remediation |
| **Otter** | **Continuous eval + governed auto-routing + audited change control + reverse routing + silent-upgrade detection** | — |

Otter positions as **"Datadog for LLM ops + PagerDuty for LLM quality + audited governance + change control"**.

## Minimum Demo Governance Checklist

Without these fields surfaced per incident, the demo looks like "the agent is smart". With them, it looks like "production change is governed".

`policy_identity` (composite: name + semver + git_sha + dmn_checksum + orchestrator_BR_version), `decision_id`, model allowlist, cost cap, PII flag, region check, rollback availability, `canary_required`, kill switch thresholds, `judge_model_version`, `rubric_version`, `baseline_version`, `baseline_maturity`, `circuit_breaker_state`, `model_identity_status`.

## UiPath Platform Integration

| UiPath Component | Otter Usage |
|------------------|-------------|
| Maestro BPMN | Top-level flow + 5 call activities + parallel multi-instance for evidence agents |
| **Business Rule Task (DMN 1.3)** | **PolicyGate — deterministic governance** |
| Automation Ops Source Control | DMN authoring synced from Git (`policies/`) |
| Agent Builder | 10 LLM agents (+ optional PolicyExplainer) |
| Action Center | Human gates: HIGH/CRITICAL routing, restore approval, postmortem review, CONTAINMENT_MODE exit |
| Assets | API keys, tenant policy bundle refs, rubric templates, judge model registry, baseline maturity flags |
| Storage Buckets | Log archives, eval sample sets, golden set, postmortem drafts, probe sets, fingerprint history |
| API Workflows | Datadog / vendor status / vendor LLM APIs |
| Queues | Incident queue (concurrent event processing) |
| Context Grounding | RAG over past incidents + runbooks for DiagnosisAgent |
| Timer / Error Boundary Events | Canary step timeouts, agent failure degradation, recovery windows |
| Message Events | Emergency kill switch global signal |

## Coding Agent Disclosure

Otter is developed using **Claude Code** (Anthropic) and **Codex** for QA review. Per UiPath AgentHack bonus criteria, prompt logs, screenshots, and conversation evidence are preserved (see `evidence/` at submission time).

## 4-Week Build Roadmap

| Week | Period | Focus |
|------|--------|-------|
| W1 | 2026-05-18 to 05-25 | Refine multi-agent supervisor → 10 LLM agents; draft DMN rule set; mock data; Pydantic schemas land |
| W2 | 2026-05-26 to 06-02 | UiPath BPMN visual editor (5 call activities); Action Center integration; **DMN/Git versioning workflow**; **BaselineProfile state store**; Assets / Connections setup |
| W3 | 2026-06-03 to 06-15 | Real Datadog / vendor status API integration; Context Grounding RAG; **CircuitBreaker state machine**; **RecoveryEvaluator + restore canary**; **ModelIdentityMonitor probe set**; edge cases; demo storyboard |
| W4 | 2026-06-16 to 06-29 | Demo video (5 min max); presentation deck; README polish; GitHub cleanup; Devpost submission |

**Bandwidth note**: W3-W4 overlaps with shipping three other hackathon tracks (Marten / Muntjac / Yuhina deadlines 6/12-15). Trade-offs to be managed carefully.

## v1 Simplifications (Hackathon Scope)

To keep the demo shippable while preserving the architectural shape:

- **Baseline maturity**: L0/L3 binary, not full L1–L4.
- **Behavioral fingerprint**: 20 probes, not full coverage.
- **Recovery monitor**: compressed timer (real windows in UI, 30–90s actual).
- **DMN release**: manual upload to Orchestrator, but `manifest.yaml` + `policy_identity` mandatory.
- **Cost ceiling**: per-incident + per-day; monthly optional.
- **Cascaded judge ensemble**: primary + arbiter only at v1; secondary judge deferred.

## Remaining Open Questions

Round 3 GPT-5 Pro consult resolved most P0/P1 questions. Audit trail in `docs/flow-questions.md`. Still open:

1. **Multi-tenancy depth for demo** — single-tenant for v1, but should we namespace UiPath Assets per `tenant_id` now to avoid v2 refactor cost?
2. **Action Center latency** — when does human-approval delay become worse than a worse-quality auto-decision? Threshold proposal needed.
3. **Synthetic baseline calibration** — for cold-start path 2 (synthetic eval), how much human calibration is required before it's better than path 1 (global baseline)?
4. **Identity probe budget at scale** — 20 probes × 4 models × hourly is fine for demo. Production-grade frequency requires cost modeling.

## Sub-Doc Index

| Doc | Owns |
|-----|------|
| [docs/lifecycle.md](docs/lifecycle.md) | 7-state incident machine, RecoveryEvaluator, reverse routing |
| [docs/policy-gate.md](docs/policy-gate.md) | PolicyGate DMN, minimum rule set, Git versioning, PolicyRuntimeIdentity |
| [docs/canary-kill-switch.md](docs/canary-kill-switch.md) | Canary subprocess, kill switch dual path, CircuitBreaker, CONTAINMENT_MODE |
| [docs/eval-drift-baseline.md](docs/eval-drift-baseline.md) | Eval methodology, Judge Health 4 signals, Baseline Maturity L0–L4 |
| [docs/model-identity.md](docs/model-identity.md) | Silent upgrade detection, 4-tier identity, 5 probe types, claim discipline |
| [docs/cost-aware-routing.md](docs/cost-aware-routing.md) | 3-layer ceiling, route classification, temporary TTL |
| [docs/flow-questions.md](docs/flow-questions.md) | Round 1–3 GPT-5 Pro consult audit trail |

## License

Apache 2.0
