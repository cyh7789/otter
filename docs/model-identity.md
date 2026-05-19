# Silent Model Upgrade Detection — ModelIdentityMonitor

> Sub-doc of `../DESIGN.md`. Owns: 4-tier identity, 5 probe types, demo-claim discipline.

## Why this matters

Vendors silently update model checkpoints. `gpt-4o` today may not be the same `gpt-4o` next week — the alias resolves to a different backend, with different behavior, often without an announcement. This is the most common cause of "the AI feature stopped working" support tickets, and no commercial LLM ops tool addresses it.

Otter is well-positioned to make this a demo-defining feature, but the claim must be controlled.

## Claim discipline

**Allowed**: "Otter detects suspected silent model changes via declared identity, provider fingerprint, and behavioral fingerprint shifts."

**Not allowed**: "Otter knows which internal checkpoint OpenAI swapped to."

We cannot prove what vendor did internally. We can prove what changed from our side. Pitch the capability, not omniscience.

## What different providers expose

Inconsistent identity surfaces — we cannot rely on one source:

- **OpenAI** — Chat Completion `response.model` shows the model actually used. `system_fingerprint` indicates backend configuration; combined with `seed` it can hint at determinism-affecting backend changes. Currently marked deprecated-optional in docs, but still emitted.
- **Anthropic** — From Claude 4.6, dateless model IDs are pinned snapshots, not evergreen pointers. Older API aliases may resolve to a dated model ID. Models API lists available models and resolves aliases to canonical IDs. Migration docs warn that token counting may change across model upgrades — affecting estimates and cost projections.
- **Google Gemini** — Explicit stable / preview / latest / experimental distinction. `latest` alias hot-swaps with each release. Google Cloud docs enumerate auto-updated aliases and their underlying stable versions.

Otter must read all of these where available, never assume one is authoritative.

## Four identity tiers

```
Tier 0  Requested identity
        What the app actually sent (e.g. gemini-flash-latest)

Tier 1  Declared response identity
        response.model, canonical model ID, alias resolution, provider headers

Tier 2  Provider fingerprint
        system_fingerprint, backend fingerprint, modelVersion, release ID
        (null when provider doesn't expose)

Tier 3  Behavioral fingerprint
        Fixed probe set under fixed harness:
          output hash / semantic distance / token count vector / refusal boundary / tool-call shape
```

Tier 0–2 are cheap, run on every request or every incident. Tier 3 is expensive, run on schedule + before routing / recovery decisions.

## Five probe types

Probe set must cover multiple fingerprint shapes — not just open-ended prompts:

1. **Exact probes**
   JSON schema extraction, classification, ordering, formatting.
   Observe: exact output hash, parse success.

2. **Semantic probes**
   Paraphrase, summarization, Q&A.
   Observe: embedding distance, judge score.

3. **Boundary probes**
   Refusal, safety, PII handling, policy edges.
   Observe: refusal rate, policy wording changes.

4. **Tool probes**
   Function calling schema, tool argument shape.
   Observe: tool name, argument JSON, field order, missing field rate.

5. **Tokenization probes**
   `count_tokens` / usage tokens vector.
   Token counting changes often accompany model upgrades; Anthropic migration docs explicitly call this out.

## Determinism caveat

`temperature = 0` does not guarantee identical outputs. Anthropic docs state this explicitly; other vendors behave similarly. Behavioral fingerprint must use:

- Repeated probes (N ≥ 5 per probe).
- Normalized output (strip whitespace, normalize JSON key order).
- Statistical distance metrics (Hamming on hashed outputs, cosine on embeddings, KL on token distributions).

Never trigger a "model swap" alert on a single hash mismatch.

## Detection confidence

```
HIGH confidence
  response_model / canonical model ID changed
  OR provider fingerprint changed AND behavioral probe shifted
  OR latest-alias now resolves to a different stable reference

MEDIUM confidence
  provider fingerprint changed only
  OR behavioral fingerprint shift > 3σ over 2 consecutive runs

LOW confidence
  Output changed but semantic distance small
  OR only latency / token usage changed
```

## ModelIdentityMonitor agent

Runs in 4 contexts:

| Trigger | Action |
|---------|--------|
| Independent Timer Start (hourly / daily) | Full probe set, persist fingerprint |
| Per-incident at intake | Lightweight identity check on source model |
| Before routing decision | Verify target model identity not in `unknown` state |
| Before recovery decision | Verify original model identity unchanged since route-out |

PolicyGate reads `ModelIdentityDriftSignal.action`:

- `record_only` — log, no impact on routing.
- `shadow_eval` — run extra eval before any decision.
- `trigger_incident` — open Otter incident automatically.
- `block_auto_route` — PolicyGate denies auto, requires human.
- `require_human` — Action Center gate.

For restore (see `lifecycle.md` Condition 3), if original model identity shifted, restore must escalate to HUMAN_REVIEW.

## Schema

```python
from typing import Literal
from pydantic import BaseModel


class ProviderIdentitySnapshot(BaseModel):
    provider: str
    requested_model: str
    response_model: str | None
    canonical_model_id: str | None
    provider_fingerprint: str | None
    provider_headers: dict[str, str] = {}
    observed_at: str


class ProbeResult(BaseModel):
    probe_id: str
    probe_set_version: str
    prompt_hash: str
    output_hash: str | None
    normalized_output_hash: str | None
    parse_success: bool | None
    semantic_embedding_ref: str | None
    token_usage: dict[str, int] = {}
    latency_ms: int
    refusal: bool | None
    tool_call_shape_hash: str | None


class BehavioralFingerprint(BaseModel):
    model_identity_key: str
    probe_set_version: str
    output_hash_vector: list[str]
    token_count_vector: list[int]
    semantic_centroid_ref: str
    tool_shape_hash_vector: list[str]
    refusal_rate: float
    fingerprint_hash: str


class ModelIdentityDriftSignal(BaseModel):
    provider: str
    requested_model: str
    baseline_identity_id: str
    current_identity: ProviderIdentitySnapshot

    declared_identity_changed: bool
    provider_fingerprint_changed: bool | None
    behavioral_shift_score: float
    token_usage_shift_score: float | None

    confidence: Literal["low", "medium", "high"]
    action: Literal[
        "record_only",
        "shadow_eval",
        "trigger_incident",
        "block_auto_route",
        "require_human",
    ]
    reasons: list[str]
```

## v1 simplifications

- Behavioral fingerprint uses **20 probes**, not full coverage.
- Schedule: hourly probe is fine; per-incident check stays lightweight.
- Demo shows declared identity drift + one behavioral shift case (Gemini `latest` alias swap is good narrative).

## Trade-off

ModelIdentityMonitor costs probe-set tokens per run. At 20 probes × 4 models × hourly = manageable. Full coverage in v2 will need cost projections; v1 keeps probe count low.

## Cross-refs

- Identity acceptance for restore: `lifecycle.md` (Condition 3)
- Identity-based routing block: `policy-gate.md` (consumes `ModelIdentityDriftSignal.action`)
- Behavioral probe overlap with judge calibration anchors: `eval-drift-baseline.md`
