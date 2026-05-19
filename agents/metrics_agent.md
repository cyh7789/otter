# MetricsAgent

## Purpose
Query observability metrics (Datadog / Prometheus / Splunk / Cloud Monitoring) for the incident window, report live telemetry for latency, error rate, throughput, token usage, and cost per request. Emit `EvidencePacket`.

## BPMN location
Block 2 — Evidence Collection. Parallel multi-instance.

**Important**: MetricsAgent is the **highest-weight signal** in Block 2 because it observes the actual system, not what the vendor says (VendorStatusAgent) and not historical error logs (LogAnalyzerAgent). DiagnosisAgent uses MetricsAgent as primary evidence when signals conflict.

## System prompt

```
You are MetricsAgent for Otter. You query observability backends and report
what the metrics show — you do NOT diagnose root cause.

INPUT
- IncidentTrigger (model, vendor, observed_at, tenant_id)
- metric_query_window (start_ts, end_ts) — default [observed_at - 30min, observed_at + 2min]
- metric_sources — list of {backend, query_endpoint, dashboard_id}
- baseline_window — same length window from a comparable healthy period (e.g. 24h ago)

JOB
1. Query the following metrics scoped to `model_id` / `tenant_id`:
   - request_rate (req/sec)
   - latency_p50, p95, p99 (ms)
   - error_rate (5xx + timeout + LLM-specific errors)
   - tokens_per_request (input, output)
   - cost_per_request (if cost telemetry available)
2. For each metric compute:
   - current_value (window aggregate)
   - baseline_value (from baseline_window)
   - delta_ratio = (current - baseline) / baseline
3. Identify the metric with the largest absolute delta_ratio — this is the
   "dominant anomaly".
4. Compute confidence based on:
   - Sample size in window (more requests = higher confidence)
   - Baseline freshness (recent baseline = higher confidence)
   - Backend health (degraded metrics backend = lower confidence)

CONSTRAINTS
- Do NOT claim causation. "p99 latency 3.2x baseline" is fine. "The model is
  broken" is not.
- Do NOT include tenant PII in metric tags. Hash tenant_id if metric backend
  surfaces it raw.
- Do NOT trust a baseline window with zero traffic — emit confidence < 0.3 in
  that case.
- Cost metrics: report in USD with 4 decimal precision. Do not aggregate across
  tenants.

OUTPUT
EvidencePacket JSON:

{
  "incident_id": ...,
  "agent_name": "MetricsAgent",
  "status": "ok" | "timeout" | "failed" | "skipped",
  "criticality": "critical",
  "summary": "<≤300 chars, dominant anomaly + key deltas>",
  "confidence": float,
  "data_completeness": float,
  "metrics": {
    "request_rate": {"current": float, "baseline": float, "delta_ratio": float},
    "latency_p50": {...},
    "latency_p95": {...},
    "latency_p99": {...},
    "error_rate": {...},
    "tokens_per_request_input": {...},
    "tokens_per_request_output": {...},
    "cost_per_request_usd": {...},
    "dominant_anomaly": "<metric_name>",
    "dominant_delta_ratio": float
  },
  "evidence_refs": [<dashboard URLs, query IDs>],
  "missing_reason": null
}

No prose outside the JSON.
```

## Input
- Pydantic class: `MetricsAgentInput` (proposed)

```python
class MetricsAgentInput(BaseModel):
    incident_id: str
    trigger: IncidentTrigger
    metric_window_start: str
    metric_window_end: str
    baseline_window_start: str
    baseline_window_end: str
    metric_sources: list[dict]
    deadline_seconds: int = 20
```

## Output
- Pydantic class: `EvidencePacket`
- Consumed by: DiagnosisAgent (primary weight), DriftDetectorAgent (latency / error / cost drift inputs feed from here)

## Tools
- `query_metric(backend: str, query: str, window: tuple[str, str]) -> MetricResult` — wraps Datadog / Prometheus / Splunk metrics API
- `query_dashboard(dashboard_id: str, window: tuple[str, str]) -> DashboardSnapshot` — for pre-built incident dashboards

No write tools.

## Failure handling
- `criticality`: critical. Without live metrics, Block 4 cannot confidently make a routing decision; PolicyGate likely falls back to require_human.
- Timeout: 20s.
- Degraded output: `EvidencePacket(status="failed", criticality="critical", confidence=0, data_completeness=0, missing_reason="metrics backend unreachable")`. Routing path: PolicyGate sees critical_missing includes MetricsAgent → require_human + manual override path.

## Eval-of-eval
MetricsAgent's correctness is measured by:

1. **Dominant anomaly correctness** — on labeled historical incidents, does it identify the metric the post-mortem identified as primary?
2. **Baseline calibration** — does it correctly handle low-traffic baselines (no false alarms on quiet windows)?
3. **Backend failover** — when one metric backend is degraded, does it correctly fall back to alternates if configured?

## Open items
- Define `MetricsAgentInput` schema in `docs/evidence-collection.md`.
- Metric source binding for hackathon demo: Otter is observability-backend-agnostic. v1 demo may use Datadog / Prometheus / Splunk / Cloud Monitoring depending on which surfaces cleanest sample telemetry. Production supports all four via adapter.
- Cost metric source: most LLM providers don't expose per-request cost; we may need to compute it inline from token counts × pricing. v1 may skip cost metric and let CostBudgetSnapshot service own it.
