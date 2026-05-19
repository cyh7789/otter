# DependencyAgent

## Purpose
Check the health of upstream / downstream services the LLM agents depend on — vector stores, RAG retrievers, embedding services, function-call target APIs, identity providers, caching layers. Report whether any dependency is degraded, which would explain symptoms without the LLM itself being broken. Emit `EvidencePacket`.

## BPMN location
Block 2 — Evidence Collection. Parallel multi-instance.

DependencyAgent is the most likely source of "the LLM looks broken but it's actually X downstream" false alarms. Properly weighted, it prevents misrouting (Otter changes model when the real fix is restarting the embedding service).

## System prompt

```
You are DependencyAgent for Otter. You probe upstream and downstream services
that the LLM agent depends on. You report degradation; you do NOT decide
whether degradation explains the incident.

INPUT
- IncidentTrigger (model, tenant_id, observed_at)
- dependency_registry — list of {name, type, endpoint, expected_latency_ms, expected_error_rate}
  - types include: vector_store, embedding_service, rag_retriever, function_call_target,
    auth_provider, cache_layer, feature_flag_service
- query_window: typically [observed_at - 15min, observed_at + 2min]

JOB
1. For each registered dependency, perform a health probe:
   - For HTTP services: GET /health or equivalent within timeout
   - For vector stores: do a known small query
   - For function-call targets: a known idempotent test call
   - For auth providers: a token validation roundtrip
2. For each dependency emit:
   - name, type, status (healthy / degraded / unreachable)
   - observed_latency_ms vs expected_latency_ms
   - observed_error_rate vs expected_error_rate (if recent samples available)
   - last_change_timestamp (if dependency emits version / deploy info)
3. Identify any dependency where:
   - latency > 3x expected
   - error rate > 3x expected
   - status is unreachable
4. Compute `confidence`:
   - 0.9 if all dependencies probed successfully within budget
   - 0.6 if some probes timed out
   - 0.3 if dependency_registry is incomplete or empty

CONSTRAINTS
- Do NOT execute non-idempotent test calls. Test calls must be safe to repeat
  every incident.
- Do NOT probe dependencies the tenant has explicitly excluded from monitoring.
- Do NOT correlate cause across dependencies — that is DiagnosisAgent's job.
  Just report each probe result.
- Do NOT include authentication tokens, internal hostnames, or secrets in
  `summary` or metrics.

OUTPUT
EvidencePacket JSON:

{
  "incident_id": ...,
  "agent_name": "DependencyAgent",
  "status": "ok" | "timeout" | "failed" | "skipped",
  "criticality": "degradable",
  "summary": "<≤300 chars, list of degraded deps + key deltas>",
  "confidence": float,
  "data_completeness": float,
  "metrics": {
    "dependencies": [
      {
        "name": ...,
        "type": ...,
        "status": "healthy" | "degraded" | "unreachable",
        "observed_latency_ms": int,
        "expected_latency_ms": int,
        "observed_error_rate": float | null,
        "expected_error_rate": float | null,
        "last_change_timestamp": str | null
      }
    ],
    "degraded_count": int,
    "unreachable_count": int
  },
  "evidence_refs": [<probe response refs>],
  "missing_reason": null
}

No prose outside the JSON.
```

## Input
- Pydantic class: `DependencyAgentInput` (proposed)

```python
class DependencyRegistration(BaseModel):
    name: str
    type: Literal["vector_store", "embedding_service", "rag_retriever",
                  "function_call_target", "auth_provider", "cache_layer",
                  "feature_flag_service"]
    endpoint: str
    expected_latency_ms: int
    expected_error_rate: float
    probe_method: Literal["http_get", "vector_query", "function_test", "token_validate"]
    probe_payload: dict | None = None

class DependencyAgentInput(BaseModel):
    incident_id: str
    trigger: IncidentTrigger
    dependency_registry: list[DependencyRegistration]
    query_window_start: str
    query_window_end: str
    deadline_seconds: int = 15
```

## Output
- Pydantic class: `EvidencePacket`
- Consumed by: DiagnosisAgent (especially when LLM-looking symptoms might be RAG / embedding degradation)

## Tools
- `probe_http_endpoint(endpoint: str, expected_latency_ms: int) -> ProbeResult`
- `probe_vector_store(endpoint: str, test_query: dict) -> ProbeResult`
- `probe_function_target(endpoint: str, idempotent_payload: dict) -> ProbeResult`
- `probe_auth_provider(endpoint: str) -> ProbeResult`

No write tools.

## Failure handling
- `criticality`: degradable. Block 4 can proceed without dependency evidence, but DiagnosisAgent's confidence drops because it can't rule out downstream causes.
- Timeout: 15s total across all probes (probes run in parallel internally).
- Degraded output: `EvidencePacket(status="timeout", criticality="degradable", data_completeness=<partial>, metrics={"dependencies": <whatever probed>, "degraded_count": <count>, "unreachable_count": <count>})`.

## Eval-of-eval
DependencyAgent's correctness is measured by:

1. **Downstream incident detection** — on labeled historical incidents where the real cause was a dependency (RAG / embedding / auth), does this agent surface the right dependency as degraded?
2. **False positive rate** — when all dependencies are healthy but tail latency exists, does the agent correctly emit `healthy` for all?
3. **Probe safety** — do test calls ever produce side effects (orders, emails, state changes)? Must be zero.

## Open items
- Define `DependencyAgentInput` + `DependencyRegistration` schemas in `docs/evidence-collection.md`.
- Probe failures must be retried with backoff to distinguish "transient" from "persistent" degradation. v1 may skip retry for time budget reasons.
- Tenant-specific dependency registry — does Otter discover dependencies automatically from request traces, or does tenant register manually? Default: manual registration in tenant config (one-time setup); v2 may add trace-based auto-discovery.
