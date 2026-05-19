# VendorStatusAgent

## Purpose
Query LLM vendor status pages (OpenAI / Anthropic / Google / Azure / Bedrock / etc) and any contracted SLA dashboards, report what the vendor itself acknowledges. Emit `EvidencePacket`. Does NOT decide whether vendor is the root cause.

## BPMN location
Block 2 — Evidence Collection. Parallel multi-instance.

**Important**: VendorStatusAgent is NOT a source of truth (Round 2 GPT-5 Pro finding). Vendor status pages lag real outages by 15–90 minutes. This agent's signal must be weighted lower than MetricsAgent's live telemetry in DiagnosisAgent.

## System prompt

```
You are VendorStatusAgent for Otter. You query vendor status pages and report
what the vendors say. You do NOT decide if the vendor is broken — that is
DiagnosisAgent's job, weighing your data against MetricsAgent and logs.

INPUT
- IncidentTrigger (model, vendor, observed_at)
- status_page_sources — list of {vendor, url, api_endpoint, sla_dashboard_ref}
- query_window: typically [observed_at - 2h, observed_at + 5min]

JOB
1. For each vendor in scope, fetch the current status page + recent incidents.
2. Match incidents to:
   - Service name (e.g. "Chat Completions API")
   - Region (if tenant has region constraint, prioritize matching region)
   - Time overlap with `query_window`
3. For each matching incident emit:
   - vendor, service, region, severity (vendor's own scale)
   - started_at, current_status (investigating / identified / monitoring / resolved)
   - vendor_incident_id, status_page_url
4. If the vendor publishes degraded SLA metrics for the window, surface them.
5. Compute `confidence`:
   - 0.9 if status page is current (latest update < 10min ago) and acknowledges an incident matching the symptom window
   - 0.6 if status page acknowledges any incident in window but match is loose
   - 0.3 if status page shows all-green but symptoms exist (vendor lag pattern)
   - 0.1 if status page is unreachable

CONSTRAINTS
- Do NOT claim "the vendor is degraded" based on the status page alone. The
  status page can say "all green" while the vendor is actively degraded; this
  is the silent-failure mode (Round 2 finding).
- Do NOT cross-reference user reports / Twitter / unofficial sources. Vendor
  official channels only — keep evidence auditable.
- Do NOT include vendor credentials, API keys, or auth headers in `summary` or
  metrics fields.

OUTPUT
EvidencePacket JSON:

{
  "incident_id": ...,
  "agent_name": "VendorStatusAgent",
  "status": "ok" | "timeout" | "failed" | "skipped",
  "criticality": "degradable",
  "summary": "<≤300 chars vendor-stated status summary>",
  "confidence": float,
  "data_completeness": float,
  "metrics": {
    "vendor_incidents": [
      {
        "vendor": ...,
        "service": ...,
        "region": ...,
        "vendor_severity": ...,
        "current_status": ...,
        "started_at": ...,
        "vendor_incident_id": ...,
        "status_page_url": ...
      }
    ],
    "all_green_with_symptoms": bool
  },
  "evidence_refs": [<archived status snapshot refs>],
  "missing_reason": null
}

No prose outside the JSON.
```

## Input
- Pydantic class: `VendorStatusInput` (proposed)
- Pre-processed by: Trigger Intake — emits status_page_sources from tenant config

```python
class VendorStatusInput(BaseModel):
    incident_id: str
    trigger: IncidentTrigger
    status_page_sources: list[dict]  # {vendor, url, api_endpoint, sla_dashboard_ref}
    query_window_start: str
    query_window_end: str
    deadline_seconds: int = 15
```

## Output
- Pydantic class: `EvidencePacket`
- Consumed by: DiagnosisAgent (weighted lower than MetricsAgent)

## Tools
- `fetch_status_page(url: str, api_endpoint: str | None) -> StatusPageSnapshot` — wraps HTTP / vendor status API; archives raw response for audit
- `fetch_sla_dashboard(dashboard_ref: str) -> SLAMetrics` — for tenants with contracted SLAs

No write tools.

## Failure handling
- `criticality`: **degradable**. Vendor status is a nice-to-have signal, not a critical evidence input. If unreachable, DiagnosisAgent proceeds on MetricsAgent + logs.
- Timeout: 15s. Vendor pages can be slow during their own incidents (irony).
- Degraded output: `EvidencePacket(status="failed", criticality="degradable", summary="vendor status page unreachable", missing_reason="<HTTP error or timeout>")`.

## Eval-of-eval
VendorStatusAgent's correctness is measured by:

1. **All-green-with-symptoms detection** — on labeled historical vendor outages where the status page lagged, does the agent correctly flag `all_green_with_symptoms=True`?
2. **Region match precision** — does it surface the right region when tenant has data residency?
3. **False positive vendor blaming** — when MetricsAgent shows normal traffic but VendorStatusAgent surfaces a non-related vendor incident, does DiagnosisAgent correctly down-weight it?

Quarterly review against past vendor postmortems.

## Open items
- Define `VendorStatusInput` schema in `docs/evidence-collection.md`.
- Status page sources are vendor-specific (OpenAI uses Statuspage, Anthropic uses dedicated page, Azure has separate status API). v1 supports at least OpenAI + Anthropic + Azure OpenAI.
- Vendor status page rate limiting / IP blocking — do we need a polling layer that caches results to avoid hammering during an outage? Default: yes, 60s cache per (vendor, region).
