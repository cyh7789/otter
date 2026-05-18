# Otter Design Document

**UiPath AgentHack 2026 — Track 2 (Maestro BPMN)**

## Problem Statement

Enterprises adopting LLM-based agents face two silent failure modes:

1. **Vendor outages** — OpenAI / Anthropic / Google API failures, status pages lag behind, fallback logic scattered across services
2. **Silent quality regression** — Underlying model updates change behavior; hallucination rates creep up; tone drifts; "the model is technically responding" but quality has degraded

Existing tooling addresses only the first (LiteLLM, Portkey, OpenRouter) via manual routing rules. No standard solution exists for **proactive continuous evaluation + automatic re-routing based on quality signals**.

## Core Concept

**Continuous LLM Agent Evaluation & Auto-Routing**

Two protection triggers, unified orchestration:

| Trigger | When | Response |
|---------|------|----------|
| **Reactive** | Vendor outage / timeout / error rate spike | Fallback to backup model |
| **Proactive** | Eval score drift / hallucination uptick / tone change | Auto-route to higher-quality model |

User promise: **"Whether the vendor breaks or the model silently degrades, your quality stays protected."**

## Multi-Agent Architecture (9 Agents)

```
START (incident webhook OR scheduled eval trigger)
  ↓
[Parallel gateway — fan out 4 investigation agents]
  ├→ LogAnalyzerAgent         (application logs)
  ├→ VendorStatusAgent        (LLM provider status pages)
  ├→ MetricsAgent             (Datadog / Prometheus: latency / cost / error rate)
  └→ DependencyAgent          (upstream service health)
[Join gateway]
  ↓
EvalAgent                     (LLM-as-judge multi-rubric on sampled production responses)
  ↓
DriftDetectorAgent            (statistical drift detection: KS test / CUSUM / composite signal)
  ↓
DiagnosisAgent                (synthesize all signals → root cause hypothesis + severity)
  ↓
[Exclusive gateway — severity]
  ├→ HIGH: RoutingDecisionAgent → Human approve (Action Center) → Execute model switch
  └→ LOW:  RoutingDecisionAgent → Auto-route + log
  ↓
NotificationAgent             (on-call + customer SLA-breach alerts)
  ↓
PostMortemAgent               (draft incident report + recommendations)
  ↓
[Human review (Action Center)]
  ↓
END
```

CanaryAgent runs on a parallel scheduled flow: progressive traffic shift + monitoring whenever a new model is introduced.

## Eval Methodology

**Hybrid signals**:

- **Primary**: LLM-as-judge with multi-rubric (accuracy, relevance, hallucination, safety, coherence, tone, format) — 1-5 score per dimension, weighted aggregate
- **Secondary**: User feedback signals (thumbs up/down, retry rate, conversation length)
- **Cost-performance**: latency p99, cost per request, error rate
- **Reference-based** (RAG context only): faithfulness check against source documents

**Implementation challenges + mitigations**:

| Challenge | Mitigation |
|-----------|-----------|
| Judge model bias (Claude judges favor Claude) | Cross-judge ensemble (Claude + GPT + Gemini, mean score) |
| Judge cost (eval every response is too expensive) | Sample 5-10% of production traffic, focus on critical paths |
| Rubric specificity (varies by domain) | Per-customer customizable rubric template stored in UiPath Assets |
| Ground truth scarcity | Continuous baseline drift detection (compare against own historical scores) |

## Drift Detection Methodology

**Drift taxonomy**:

1. **Data drift** — input distribution shifts (user prompts change)
2. **Concept drift** — same input, model output changes (vendor silently updated underlying model)
3. **Performance drift** — eval score declines (regardless of input)
4. **Behavior drift** — tone / format / hallucination rate changes

**Quantitative thresholds (example baselines)**:

| Signal | Threshold |
|--------|-----------|
| Eval score rolling 24h average | drop > 5% vs baseline → alert |
| Latency p99 | > 2x baseline → alert |
| Error rate | > 1% sustained > 5 min → alert |
| Hallucination rate (judge-detected) | > 3% → alert |
| Cost per response | > 2x baseline → alert |

**Statistical methods**:

- **KS test** on score distributions (current window vs baseline)
- **CUSUM** (cumulative sum) for slow drift detection
- **Bayesian online change-point detection**

**Detection strategy — hybrid**:

1. **Golden dataset**: fixed 100 representative prompts, replay hourly/daily, score variance > 10% → alert
2. **Production sample**: 5-10% sampled traffic, rolling 6h window, significance test (p < 0.05) → alert
3. **Multi-signal aggregation**: weighted composite score, avoids single-signal false alarms

## UiPath Platform Integration

Components leveraged for Platform Usage scoring depth:

| UiPath Component | Otter Usage |
|------------------|-------------|
| **Maestro BPMN** | Full orchestration flow (parallel + sequential + exclusive gateways + loops) |
| **Agent Builder** | 9 specialist agents |
| **API Workflows** | Datadog / OpenAI status / vendor LLM APIs |
| **Action Center** | 2 human-in-the-loop gates (HIGH severity approval, post-mortem review) |
| **Assets** | API keys (Datadog / PagerDuty / vendor LLMs), customer rubric templates |
| **Storage Buckets** | Log archives, post-mortem drafts, golden dataset |
| **Connections** | External service connectors |
| **Queues** | Incident queue (concurrent event processing) |
| **Context Grounding** | RAG over past incidents + runbooks for DiagnosisAgent |

## Differentiation vs Commercial Tools

| Tool | What It Does | What It Lacks |
|------|--------------|---------------|
| LiteLLM / Portkey | Manual routing rules | No continuous evaluation |
| Helicone | Metrics collection | No automatic routing decisions |
| LangSmith / Langfuse | Tracing + eval | No auto-routing, no orchestration of remediation |
| **Otter** | **Agent-based continuous eval + auto-routing + orchestrated remediation** | — |

Otter positions as **"Datadog for LLM ops + PagerDuty for LLM quality" combined**.

## Coding Agent Disclosure

Otter is developed using **Claude Code** (Anthropic) and **Codex** for QA review. Per UiPath AgentHack bonus criteria, prompt logs, screenshots, and conversation evidence are preserved in this repository (see `evidence/` directory at submission time).

## 4-Week Build Roadmap

| Week | Period | Focus |
|------|--------|-------|
| W1 | 2026-05-18 to 05-25 | Refine multi-agent supervisor, mock data, 9-agent prompt engineering |
| W2 | 2026-05-26 to 06-02 | UiPath BPMN visual editor flow, Action Center integration, Assets / Connections setup |
| W3 | 2026-06-03 to 06-15 | Real Datadog / vendor status API integration, Context Grounding RAG, edge cases, demo storyboard |
| W4 | 2026-06-16 to 06-29 | Demo video recording (5 min max), presentation deck, README polish, GitHub cleanup, Devpost submission |

**Bandwidth note**: W3-W4 overlaps with shipping of three other hackathon tracks (Marten / Muntjac / Yuhina deadlines 6/12-15). Trade-offs to be managed carefully.

## Open Design Questions

1. **Cross-judge ensemble cost**: 3x judge calls per eval — affordable at 5% sampling, but at higher rates?
2. **Customer onboarding for rubrics**: who writes the per-domain rubric — engineer or business stakeholder?
3. **Baseline establishment**: cold-start problem — how does Otter establish baselines for a fresh deployment?
4. **Action Center latency**: human-approval gate latency vs auto-decision — when does human review block too long?

## License

Apache 2.0
