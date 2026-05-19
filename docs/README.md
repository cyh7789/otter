# Otter — Design Sub-Docs

Top-level index + architectural decisions live in [`../DESIGN.md`](../DESIGN.md). Each sub-doc below owns one domain end-to-end: prose + schema + cross-refs.

## How to read

| Start here if you want to understand… | Read |
|---------------------------------------|------|
| The whole picture, fast | [`../DESIGN.md`](../DESIGN.md) — index + Component Inventory + 5-block BPMN |
| How an incident moves from detection to resolution | [`lifecycle.md`](lifecycle.md) |
| Why PolicyGate is not an LLM agent, and how DMN is versioned | [`policy-gate.md`](policy-gate.md) |
| Canary, kill switch dual path, runaway loop prevention | [`canary-kill-switch.md`](canary-kill-switch.md) |
| Eval methodology, judge health, cold-start baseline gating | [`eval-drift-baseline.md`](eval-drift-baseline.md) |
| Silent model upgrade detection (demo killer feature) | [`model-identity.md`](model-identity.md) |
| Cost ceilings, route classification, temporary TTL | [`cost-aware-routing.md`](cost-aware-routing.md) |
| Why each design decision was made (Round 1–3 GPT-5 Pro consult) | [`flow-questions.md`](flow-questions.md) |

## Conventions

- **Schemas inline** — each sub-doc embeds the Pydantic models it owns. Cross-doc references point to the sub-doc that authors the type.
- **One topic per file** — if a topic genuinely spans two domains (e.g. PolicyGateInput needs CostBudgetSnapshot), the authoring doc owns the type and consumers link to it.
- **No `schemas.md`** — the previous monolithic schema dump is retired. Per-domain layout makes maintenance feasible at this size.

## Cross-doc dependency map

```
                lifecycle.md
                  ↑    ↓
                  │   uses RouteDirection from
                  │
policy-gate.md ←──┼──→ canary-kill-switch.md
   ↑              │           ↑
   │              │           │
   │ reads        │           │ reads CircuitBreakerState
   │              │           │
   └──── eval-drift-baseline.md ──────┐
                  │                    │
                  └──→ model-identity.md
                  │
                  └──→ cost-aware-routing.md
```

In words:

- **lifecycle.md** orchestrates everything. It defines `RouteDirection` consumed by `canary-kill-switch.md` and `policy-gate.md`.
- **policy-gate.md** reads `CircuitBreakerState` (`canary-kill-switch.md`), `BaselineReadinessDecision` (`eval-drift-baseline.md`), `ModelIdentityDriftSignal` (`model-identity.md`), and `CostBudgetSnapshot` (`cost-aware-routing.md`).
- **canary-kill-switch.md** holds Block 5 execution; receives `ApprovedRoutingDecision` and `RecoveryDecision`.
- **eval-drift-baseline.md** authors `EvidencePacket` / `EvidenceBundle` (Block 2) and all eval / drift / judge / baseline types.
- **model-identity.md** runs on its own timer + at routing/recovery decision points.
- **cost-aware-routing.md** authors `CostBudgetSnapshot` and route classification.

## Adding new domains

When a new orthogonal concern appears (e.g. multi-tenancy depth, customer-facing explainability), follow the same pattern:

1. New sub-doc under `docs/`.
2. Add a row to the index in `../DESIGN.md`.
3. Add a row to "How to read" above.
4. Cross-link from any sub-doc that reads / writes the new domain's types.
