# Otter Agent Specifications

Per-agent implementation specs for the 10 LLM agents in DESIGN.md §Component Inventory.

Each agent file in this directory owns:
- **System prompt** — the actual instruction text passed to the LLM
- **Input / output schema references** — pointers to Pydantic models defined in `../docs/*.md`
- **Tool calls** — what tools the agent may invoke
- **Failure handling** — what `criticality` is, what the degraded output looks like
- **Eval-of-eval** — how this agent itself is monitored

Schemas live in `../docs/*.md` (single source). Do not duplicate them here — reference by class name.

## Index

| # | Agent | Block | Sub-doc | Spec file |
|---|-------|-------|---------|-----------|
| 1 | LogAnalyzerAgent | 2 Evidence | — | `log_analyzer_agent.md` (TODO) |
| 2 | VendorStatusAgent | 2 Evidence | — | `vendor_status_agent.md` (TODO) |
| 3 | MetricsAgent | 2 Evidence | — | `metrics_agent.md` (TODO) |
| 4 | DependencyAgent | 2 Evidence | — | `dependency_agent.md` (TODO) |
| 5 | **EvalAgent** | 3 Eval+Drift | `eval-drift-baseline.md` | `eval_agent.md` |
| 6 | **DriftDetectorAgent** | 3 Eval+Drift | `eval-drift-baseline.md` | `drift_detector_agent.md` |
| 7 | DiagnosisAgent | 4 Decision | — | `diagnosis_agent.md` (TODO) |
| 8 | **RoutingDecisionAgent** | 4 Decision | `cost-aware-routing.md` | `routing_decision_agent.md` |
| 9 | RecoveryEvaluator | lifecycle.RESTORE | `lifecycle.md` | `recovery_evaluator.md` (TODO) |
| 10 | ModelIdentityMonitor | independent timer | `model-identity.md` | `model_identity_monitor.md` (TODO) |
| — | PolicyExplainerAgent | 4 Decision (optional) | `policy-gate.md` | `policy_explainer_agent.md` (TODO) |
| — | NotificationAgent | 5 Canary async tail | — | `notification_agent.md` (TODO) |
| — | PostMortemAgent | 5 Canary async tail | — | `post_mortem_agent.md` (TODO) |

W1 priority (5/19–5/25): bold rows above — 3 most schema-complete agents written first as templates.

## Spec file template

```markdown
# <AgentName>

## Purpose
One sentence. What this agent decides / produces.

## BPMN location
Block N, role in subprocess, sync/async.

## System prompt
The verbatim instruction text passed to the LLM. Include:
- Role
- Inputs (reference Pydantic class names)
- Decision rules
- Output schema (reference)
- Constraints (what NOT to decide)

## Input
- Pydantic class: `<ClassName>` from `../docs/<sub-doc>.md`
- Pre-processed by: <upstream block>

## Output
- Pydantic class: `<ClassName>` from `../docs/<sub-doc>.md`
- Consumed by: <downstream block>

## Tools
- List of tool functions the agent can call. Empty list if pure transform.

## Failure handling
- `criticality`: critical | degradable
- Timeout: <seconds>
- Degraded output: what to emit if cannot run

## Eval-of-eval
- How this agent itself is monitored (judge health for judges, probe coverage for monitors, etc).
```
