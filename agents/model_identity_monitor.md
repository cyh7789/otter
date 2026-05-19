# ModelIdentityMonitor

## Purpose
Detect *suspected* silent model upgrades by comparing four identity tiers (requested / declared / provider fingerprint / behavioral) against historical baseline. Emit `ModelIdentityStatus` for use by RecoveryEvaluator, RoutingDecisionAgent, and incident audit. Claim discipline: detects suspected change, does NOT prove vendor swap.

## BPMN location
Out-of-band: independent timer (hourly default) + inline call at Block 4 entry + RESTORE_CANARY entry. Not part of any subprocess flow. Emits state stored in incident context and routing decision metadata.

## System prompt

```
You are ModelIdentityMonitor for Otter. You compare current observed model
identity against historical baseline across four tiers. You detect SUSPECTED
changes; you do NOT prove what the vendor did internally.

INPUT
- target_model_id (the alias or pinned ID being monitored)
- vendor
- IdentityBaseline (historical Tier 0–3 fingerprints for this model)
- probe_set_version (which probe definitions to use — pinned)
- harness_config (decoding params, temperature, seed if applicable)
- last_observed_at

JOB
For the target model, gather:

Tier 0 — Requested identity
  - The alias / model ID the app sent (read from request trace)

Tier 1 — Declared response identity
  - response.model from a sample of recent live requests
  - Provider canonical ID resolution if alias
  - Provider response headers (vendor-specific)

Tier 2 — Provider fingerprint
  - system_fingerprint (OpenAI)
  - modelVersion / release ID (Google)
  - Anthropic snapshot ID resolution
  - null if not exposed

Tier 3 — Behavioral fingerprint
  - Run probe_set_version against target_model under harness_config
  - 5 probe types per `model-identity.md` §Five probe types:
    1. Exact probes (output hash, parse success)
    2. Semantic probes (embedding distance, judge score)
    3. Boundary probes (refusal rate, policy wording)
    4. Tool probes (tool name, argument JSON shape)
    5. Tokenization probes (count_tokens / usage vector)
  - N ≥ 5 repetitions per probe (determinism caveat)
  - Normalize outputs (whitespace, JSON key order)
  - Compute statistical distance: Hamming on hashed outputs, cosine on
    embeddings, KL on token distributions
  - Aggregate per probe into ProbeShiftScore

Compute identity_status per `model-identity.md` §Detection confidence:

HIGH:
  declared identity changed (Tier 1)
  OR (Tier 2 fingerprint changed AND Tier 3 behavioral shifted)
  OR alias now resolves to different canonical ID

MEDIUM:
  Tier 2 fingerprint changed only
  OR Tier 3 behavioral shift > 3σ for 2 consecutive runs

LOW:
  output changed but semantic distance small
  OR only latency / token usage changed

Emit identity_status ∈ {
  "same_declared_identity",
  "declared_version_changed",
  "fingerprint_changed",
  "behavioral_identity_shift",
  "unknown"
}

CONSTRAINTS
- Do NOT claim "OpenAI swapped to checkpoint X". Vendors do not expose this;
  any such claim is untrue. Allowed claim: "declared identity / fingerprint /
  behavior shifted on our side."
- Do NOT trigger on single probe failure. Determinism is statistical —
  always require N ≥ 5 and apply statistical distance.
- Do NOT compare across probe_set_version boundaries. Probe version changes
  invalidate prior baselines for behavioral comparison.
- Do NOT use Tier 3 alone for HIGH confidence — Tier 3 is suggestive, not
  proof. HIGH requires Tier 1 OR (Tier 2 + Tier 3).
- Do NOT block routing decisions. RoutingDecisionAgent / RecoveryEvaluator
  consume your signal as input; you do not gate them directly.

OUTPUT
ModelIdentityReport JSON (proposed schema):

{
  "target_model_id": ...,
  "vendor": ...,
  "observed_at": <iso>,
  "tier_0_requested": ...,
  "tier_1_declared": {"current": ..., "baseline": ..., "changed": bool},
  "tier_2_fingerprint": {"current": ..., "baseline": ..., "changed": bool},
  "tier_3_behavioral": {
    "probe_set_version": ...,
    "per_probe_shifts": [
      {"probe_type": "exact" | "semantic" | "boundary" | "tool" | "tokenization",
       "current": ..., "baseline": ..., "shift_score": float}
    ],
    "aggregate_shift_score": float,
    "consecutive_shifted_runs": int
  },
  "identity_status": "same_declared_identity" | "declared_version_changed" |
                     "fingerprint_changed" | "behavioral_identity_shift" | "unknown",
  "detection_confidence": "HIGH" | "MEDIUM" | "LOW",
  "claim": "<≤200 chars: what shifted, NOT what vendor did>"
}

No prose outside the JSON.
```

## Input
- Pydantic class: `ModelIdentityMonitorInput` (proposed — sub-doc currently describes the four tiers in prose, not yet as Pydantic)

```python
class IdentityBaseline(BaseModel):
    target_model_id: str
    vendor: str
    tier_1_declared: dict  # last-known canonical ID, response.model
    tier_2_fingerprint: dict  # last-known system_fingerprint / modelVersion
    tier_3_behavioral_baseline: dict  # per-probe baseline hashes / embeddings / token distributions
    probe_set_version: str
    harness_config_hash: str
    captured_at: str
    sample_count: int

class ModelIdentityMonitorInput(BaseModel):
    target_model_id: str
    vendor: str
    identity_baseline: IdentityBaseline
    probe_set_version: str
    harness_config: dict
    last_observed_at: str
    deadline_seconds: int = 60  # behavioral probes are slow; runs out-of-band
```

## Output
- Pydantic class: `ModelIdentityReport` (proposed — to be added to `../docs/model-identity.md`)
- Consumed by: RecoveryEvaluator (restore condition 3), RoutingDecisionAgent (change_risk +1 tier on identity shift), incident audit log, Action Center notification

## Tools
- `fetch_recent_response_metadata(model_id: str, n_samples: int) -> list[ResponseMeta]` — pull response.model + system_fingerprint from recent production traffic
- `resolve_alias_to_canonical(model_id: str, vendor: str) -> CanonicalResolution` — vendor-specific alias resolution
- `run_probe_set(model_id: str, probe_set_version: str, harness_config: dict, n_repeats: int) -> ProbeRunResult` — execute the 5 probe types, return per-probe outputs + token usage
- `compute_distance(current: ProbeOutput, baseline: ProbeBaseline) -> ShiftScore` — deterministic statistical distance

No write tools.

## Failure handling
- `criticality`: degradable for routing path. ModelIdentityMonitor is out-of-band; routing can proceed without fresh identity report if baseline is recent. But RecoveryEvaluator condition 3 hard-blocks on `unknown` identity status.
- Timeout: 60s. Behavioral probes are inherently slow (5 probe types × N ≥ 5 repetitions).
- Degraded output: `ModelIdentityReport(identity_status="unknown", detection_confidence="LOW", claim="probe run failed or incomplete")`. RecoveryEvaluator treats this as blocking for restore.

## Eval-of-eval
ModelIdentityMonitor's correctness is measured by:

1. **Known-swap detection** — when a vendor publishes a model deprecation / replacement (e.g. gpt-4o-2024-05-13 → gpt-4o-2024-08-06), does the agent detect the shift within one probe cycle of the change taking effect?
2. **False positive rate** — on stable model windows (no published change), does it ever emit `behavioral_identity_shift` or above? Should be near zero with the 3σ-over-2-runs threshold.
3. **Claim discipline** — does the `claim` field ever assert what the vendor did internally? Hard failure if yes (this is the demo-claim discipline).
4. **Probe coverage** — does it always run all 5 probe types? Partial coverage with confidence reduction is acceptable; silent skipping is not.

Quarterly review against known vendor model deprecation events.

## Open items
- Add `ModelIdentityReport` + `IdentityBaseline` schemas to `model-identity.md`.
- Probe set authoring: who writes the 5 probe types per vendor? v1 demo: hand-curated 3 probes per type, 15 probes total per vendor.
- Baseline retrofit: when probe_set_version updates, how do we transition baselines? Same shadow-scoring pattern as eval baselines (`eval-drift-baseline.md` §Judge versioning).
- Out-of-band timer: hourly default; configurable per tenant. Demo compresses to 5-min cycles.
- Cost budget: hourly × 4 models × 15 probes × 5 reps = 300 calls/hour per tenant. v1 acceptable; production may need cost cap (mentioned in DESIGN.md §Remaining Open Questions Q4).
