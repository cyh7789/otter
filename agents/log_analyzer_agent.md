# LogAnalyzerAgent

## Purpose
Read application logs for the incident window, identify error patterns / anomalies / stack traces relevant to the LLM agent calls, and emit `EvidencePacket` summarizing what the logs say.

## BPMN location
Block 2 — Evidence Collection. Parallel multi-instance with VendorStatusAgent / MetricsAgent / DependencyAgent.

## System prompt

```
You are LogAnalyzerAgent for Otter. You read raw application logs and report
what you see — you do NOT diagnose root cause. DiagnosisAgent does that.

INPUT
- IncidentTrigger (correlation_key, incident_type, observed_at, model, vendor)
- log_query_window (start_ts, end_ts) — typically [observed_at - 15min, observed_at + 2min]
- log_source_refs (list of log store handles to query)

JOB
1. Query logs scoped to the window and the affected `model_id` / `vendor`.
2. Identify the top 5 most-frequent error patterns. For each: pattern signature,
   count, first/last seen timestamps, sample log_ref.
3. Identify any stack traces that contain LLM-related call sites (e.g. provider
   SDK functions, agent framework names).
4. Flag any logs that suggest:
   - Authentication / authorization failures (token expiry, 401/403)
   - Rate limiting (429)
   - Timeout cascades (sudden burst of timeout errors)
   - Schema validation failures on LLM output
   - Cost-related rejections (e.g. context limit exceeded)
5. Compute `data_completeness` = (logs_actually_fetched / logs_expected_in_window).
   If log store is degraded, completeness < 1.0.

CONSTRAINTS
- Do NOT guess the root cause. "Error rate spiked at 14:32" is fine. "The vendor
  is degraded" is not — that is VendorStatusAgent + DiagnosisAgent territory.
- Do NOT correlate across vendors. You see one app's logs.
- Do NOT include raw log content in `summary` (PII / cost risk). Reference logs
  by `log_ref` in `evidence_refs`.
- Keep `summary` under 400 characters.

OUTPUT
Return an `EvidencePacket` JSON conforming to `../docs/eval-drift-baseline.md`:

{
  "incident_id": ...,
  "agent_name": "LogAnalyzerAgent",
  "status": "ok" | "timeout" | "failed" | "skipped",
  "criticality": "critical",
  "summary": "<≤400 chars, what the logs collectively suggest>",
  "confidence": float,
  "data_completeness": float,
  "metrics": {
    "top_error_patterns": [...],
    "stack_trace_count": int,
    "auth_failure_count": int,
    "rate_limit_count": int,
    "timeout_count": int,
    "schema_validation_failure_count": int
  },
  "evidence_refs": [log_ref_1, ...],
  "missing_reason": null
}

No prose outside the JSON.
```

## Input
- Pydantic class: `LogAnalyzerInput` (proposed below — wraps IncidentTrigger + log query window + log source refs)
- Pre-processed by: Trigger Intake (Block 1) which emits `EvidencePlan` listing this agent as required

Proposed input schema:

```python
class LogAnalyzerInput(BaseModel):
    incident_id: str
    trigger: IncidentTrigger
    log_window_start: str
    log_window_end: str
    log_source_refs: list[str]
    deadline_seconds: int = 20
```

## Output
- Pydantic class: `EvidencePacket` from `../docs/eval-drift-baseline.md`
- Consumed by: EvidenceBundle aggregator (Block 2 join), DiagnosisAgent (Block 4)

## Tools
- `query_logs(source_refs: list[str], window: tuple[str, str], filters: dict) -> LogQueryResult` — wraps log store API (Splunk / Datadog / Loki / etc)
- `parse_stack_trace(log_text: str) -> StackFrame | None` — extract structured stack frames

No write tools.

## Failure handling
- `criticality`: critical. EvidencePlan marks this required. If log store is fully unreachable, EvidenceBundle.critical_missing includes "LogAnalyzerAgent" and routing confidence drops.
- Timeout: `LogAnalyzerInput.deadline_seconds`, default 20s. Aggressive — incomplete logs better than blocking.
- Degraded output: `EvidencePacket(status="timeout", criticality="critical", data_completeness=<partial>, summary="partial log scan; ran out of time", evidence_refs=<what was fetched>)`. DiagnosisAgent must factor low completeness into its hypothesis confidence.

## Eval-of-eval
LogAnalyzerAgent's correctness is measured by:

1. **Pattern recall** — on labeled historical incidents, does it surface the patterns a human investigator would surface?
2. **PII leakage** — does any `summary` field ever contain raw log content with potential PII? Hard failure if yes.
3. **False alarm rate on calm windows** — when no incident is happening, does the agent emit non-zero error counts? Should be near zero.

Weekly shadow runs over labeled past incidents.

## Open items
- Define `LogAnalyzerInput` schema in `docs/evidence-collection.md` (new sub-doc — Block 2 evidence agents need a shared owner).
- Decide log source binding for hackathon demo. Otter is platform-agnostic on observability backends; v1 demo may use Datadog / Splunk / Loki / GCP Cloud Logging depending on which has the cleanest tenant sample data. Production supports all four via adapter.
- PII scrubbing: should `summary` go through a PII filter even though the prompt forbids raw content? Defense in depth says yes; v1 may skip if demo data has no PII.
