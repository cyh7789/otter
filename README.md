# Otter

**Governed Change Control for LLM Runtimes** — continuous evaluation, deterministic governance, and audited automatic re-routing for enterprise LLM agents.

**UiPath AgentHack 2026 — Track 2 (Maestro BPMN) submission.**

> "Whether the vendor breaks, the model silently degrades, or the vendor swaps the model underneath you — your quality stays protected, and every change is auditable."

## The Problem

Enterprises adopting LLM-based agents face two silent failure modes:

1. **Vendor outages** — OpenAI / Anthropic / Google API failures. Status pages lag behind reality. Fallback logic is scattered across services.
2. **Silent quality regression** — Underlying model updates change behavior. Hallucination rates creep up. Tone drifts. The model is technically responding, but quality has degraded — sometimes because the vendor silently swapped the underlying checkpoint.

Existing tools (LiteLLM, Portkey, OpenRouter) handle manual routing rules but offer no continuous evaluation, no governance layer, and no return-to-normal path.

## How Otter Solves It

Otter acts as **"Datadog for LLM ops + PagerDuty for LLM quality + audited governance + change control"**:

| Trigger | When | Response |
|---------|------|----------|
| **Reactive** | Vendor outage / timeout / error rate spike | Fallback to backup model |
| **Proactive** | Eval score drift / hallucination uptick / tone change / silent model identity shift | Auto-route to higher-quality model |

Key architectural decision: **the LLM proposes, but never decides.** An LLM Optimizer agent proposes candidate routes, but a deterministic Governor (DMN business rules) makes the final allow/deny. Governance decisions are reproducible, version-pinned, and auditable.

## Agent Type

**Coded Agents** — built with LangGraph (Python) + `uipath-python` SDK, deployed to UiPath Cloud Platform via `uipath pack` / `uipath publish`.

## Component Inventory

**10 LLM agents + 3 deterministic services + 1 optional explainer:**

| # | Component | Type | Responsibility |
|---|-----------|------|----------------|
| 1 | LogAnalyzerAgent | LLM | Application log analysis |
| 2 | VendorStatusAgent | LLM | LLM provider status page monitoring |
| 3 | MetricsAgent | LLM | Datadog / Prometheus metrics |
| 4 | DependencyAgent | LLM | Upstream service health |
| 5 | EvalAgent | LLM | LLM-as-judge multi-rubric evaluation |
| 6 | DriftDetectorAgent | LLM | Statistical drift + judge health |
| 7 | DiagnosisAgent | LLM | Synthesize signals into root cause + severity |
| 8 | RoutingDecisionAgent | LLM | Propose outbound candidates + cost estimate |
| 9 | RecoveryEvaluator | LLM | Propose restore candidates (return-to-normal) |
| 10 | ModelIdentityMonitor | LLM (probe runner) | Detect silent model upgrades |
| — | PolicyGate | DMN Business Rule Task | Allow / deny / require-canary / require-human |
| — | RouteCircuitBreaker | Deterministic state machine | Retry budget, circuit state, loop prevention |
| — | BaselineProfile gate | Deterministic check | Cold-start maturity verification |
| — | PolicyExplainerAgent *(optional)* | LLM | Narrate PolicyGate decision (no authority) |

## UiPath Components Used

| UiPath Component | How Otter Uses It |
|------------------|-------------------|
| **Maestro BPMN** | Top-level orchestration flow + 5 call activities + parallel multi-instance for evidence agents |
| **Business Rule Task (DMN 1.3)** | PolicyGate — deterministic governance decisions |
| **Automation Ops Source Control** | DMN authoring synced from Git (`policies/`) |
| **Agent Builder** | 10 LLM agents + optional PolicyExplainer |
| **Action Center** | Human gates: HIGH/CRITICAL routing approval, restore approval, postmortem review, CONTAINMENT_MODE exit |
| **Assets** | API keys, tenant policy bundle refs, rubric templates, judge model registry, baseline maturity flags |
| **Storage Buckets** | Log archives, eval sample sets, golden set, postmortem drafts, probe sets, fingerprint history |
| **API Workflows** | Datadog / vendor status / vendor LLM API integrations |
| **Queues** | Incident queue for concurrent event processing |
| **Context Grounding** | RAG over past incidents + runbooks for DiagnosisAgent |
| **Timer / Error Boundary Events** | Canary step timeouts, agent failure degradation, recovery windows |
| **Message Events** | Emergency kill switch global signal |

## Top-Level BPMN Flow

```
[Start: Reactive Message | Proactive Timer]
    |
(1) Trigger Intake              sync inline subprocess
    |
[Parallel gateway]
    |---> (2) Evidence Collection    sync call activity (parallel multi-instance)
    |---> (3) Eval + Drift           sync call activity (timeout, depth-configurable)
[Join]
    |
(4) Decision + Policy Gate      sync call activity
    |
(5) Canary + Monitor            sync guard window -> async observation + notify + postmortem
    |
[End]
```

## Setup Instructions

### Prerequisites

- **Python 3.11+**
- **[uv](https://docs.astral.sh/uv/)** (Python package manager)
- **Google API Key** — for LangChain + Gemini (free tier available at [aistudio.google.com](https://aistudio.google.com))
- **UiPath Cloud account** — for full deployment (local debug works without it)

### Step 1: Clone and Install

```bash
git clone https://github.com/cyh7789/otter.git
cd otter
uv sync
```

### Step 2: Configure Environment

```bash
cp .env.example .env
```

Edit `.env` and fill in:

| Variable | Required | Description |
|----------|----------|-------------|
| `GOOGLE_API_KEY` | Yes | Gemini API key from Google AI Studio |
| `UIPATH_URL` | For cloud deploy | Auto-populated by `uipath auth` |
| `UIPATH_ACCESS_TOKEN` | For cloud deploy | Auto-populated by `uipath auth` |

### Step 3: Run Locally (No UiPath Account Needed)

```bash
uv run main.py
```

This runs the full LangGraph agent pipeline locally for debugging and development.

### Step 4: Deploy to UiPath Cloud

```bash
# Authenticate with UiPath Cloud (opens browser)
uv run uipath auth

# Generate entry points from LangGraph config
uv run uipath init

# Build deployment package
uv run uipath pack

# Publish to UiPath Cloud
uv run uipath publish
```

After publishing, the agents are available in UiPath Orchestrator. Configure the Maestro BPMN flow, Action Center human gates, and Assets (API keys, policy bundles) via the UiPath Cloud dashboard.

### Step 5: Verify Deployment

1. Open **UiPath Orchestrator** and confirm the package appears under Processes
2. Open **Maestro** and verify the BPMN flow is linked
3. Trigger a test incident to confirm the end-to-end pipeline runs

## Differentiation

| Tool | What It Does | What It Lacks |
|------|--------------|---------------|
| LiteLLM / Portkey | Manual routing rules | No continuous eval, no governance, no return-to-normal |
| Helicone | Metrics collection | No automatic routing |
| LangSmith / Langfuse | Tracing + eval | No auto-routing, no orchestrated remediation |
| **Otter** | **Continuous eval + governed auto-routing + audited change control + reverse routing + silent-upgrade detection** | — |

## Coding Agent Disclosure

This project was built using **Claude Code** powered by **Claude Agent SDK** (Anthropic) as the primary coding agent, with **Codex** (OpenAI) for QA review. Per UiPath AgentHack bonus criteria, development logs and conversation evidence are preserved in the [`evidence/`](./evidence/) directory.

## Documentation

For full architectural detail, see:

- [DESIGN.md](./DESIGN.md) — Architecture decisions, component inventory, BPMN mapping
- [docs/lifecycle.md](docs/lifecycle.md) — 7-state incident machine, recovery evaluator
- [docs/policy-gate.md](docs/policy-gate.md) — PolicyGate DMN, Git versioning
- [docs/canary-kill-switch.md](docs/canary-kill-switch.md) — Canary subprocess, circuit breaker
- [docs/eval-drift-baseline.md](docs/eval-drift-baseline.md) — Eval methodology, baseline maturity
- [docs/model-identity.md](docs/model-identity.md) — Silent upgrade detection
- [docs/cost-aware-routing.md](docs/cost-aware-routing.md) — 3-layer cost ceiling

## License

Apache 2.0
