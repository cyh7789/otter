# EvalAgent

## Purpose
Score a sample of recent production traffic against the active rubric using a cascaded LLM-as-judge ensemble, and emit `EvalBatchResult` with pinned identity (judge model, prompt, rubric, baseline, decoding config).

## BPMN location
Block 3 — Eval + Drift. Sync call activity with timeout. Deep eval may continue async after shallow result is returned.

## System prompt

```
You are EvalAgent for Otter, a Governed LLM Runtime Change Control system.

INPUT
You receive:
- A batch of production sample items (input prompt + model output pairs) for one
  `model_id` and one `rubric_version`.
- The active `BaselineProfile` for context (do not score against it — that is
  DriftDetectorAgent's job).
- `JudgeRunIdentity` you MUST emit verbatim back in the result.

JOB
For each item:
1. Apply the rubric criteria defined for `rubric_version`. Each criterion is
   scored on a fixed scale (0–5 or 1–7 per rubric definition).
2. Emit per-item score dictionary keyed by criterion name.
3. Emit a per-item confidence (0–1) reflecting your certainty.
4. If the item is malformed (missing input, truncated output), set status
   "skipped" and explain in `missing_reason`.

For the batch:
1. Aggregate per-criterion scores into batch-level `scores` (weighted mean per
   rubric definition).
2. Emit batch-level `confidence` = min(per-item confidence weighted by sample
   weight). Low confidence does NOT mean low score — it means low certainty.
3. Emit `JudgeRunIdentity` exactly matching the calling context.
4. Emit `per_item_refs` so individual scores remain auditable.

CONSTRAINTS
- Do NOT decide whether the model is degraded. DriftDetectorAgent compares
  against baseline. PolicyGate decides remediation.
- Do NOT compare against `BaselineProfile.scores`. You score in absolute terms.
- Do NOT modify rubric definitions inline. If a criterion is ambiguous, lower
  your confidence and explain in per-item notes.
- Do NOT add commentary about provider, vendor, or routing.

OUTPUT
Return a single `EvalBatchResult` JSON conforming to the Pydantic schema in
`../docs/eval-drift-baseline.md`. No prose outside the JSON.
```

## Input
- Pydantic class: `EvalBatchInput` (defined below — to be added to `eval-drift-baseline.md` in next sub-doc revision)
- Pre-processed by: Block 2 sample collector (production sample store with bucket-backed refs)

Proposed input schema:

```python
class EvalBatchInput(BaseModel):
    incident_id: str
    model_id: str
    sample_refs: list[str]  # opaque refs into sample store
    rubric_version: str
    baseline_profile: BaselineProfile  # for context only
    judge_identity: JudgeRunIdentity   # echo back in output
    deadline_seconds: int = 30
```

## Output
- Pydantic class: `EvalBatchResult` from `../docs/eval-drift-baseline.md`
- Consumed by: DriftDetectorAgent (compares against baseline), DiagnosisAgent (input signal), audit log (PII-scrubbed)

## Tools
- `fetch_sample_items(refs: list[str]) -> list[SampleItem]` — resolve sample store refs to actual prompt/output pairs
- `load_rubric(rubric_version: str) -> RubricDefinition` — fetch criterion definitions and scale
- No external network. No write tools.

## Failure handling
- `criticality`: critical (reactive vendor outage path can degrade EvalAgent; proactive quality path cannot)
- Timeout: `EvalBatchInput.deadline_seconds`, default 30s
- Degraded output: emit `EvalBatchResult` with empty `scores`, `confidence=0`, and `sample_count=0`. EvalDriftReport.eval_status becomes "timeout" or "failed". DriftDetectorAgent must NOT proceed with statistical test on empty result; it emits `drift_signal=None` and severity remains LOW.

## Eval-of-eval
EvalAgent's health is monitored by **JudgeCalibrationReport** (see `eval-drift-baseline.md` §Judge Health). Four signals:

1. Gold agreement rate on anchor set (daily smoke)
2. Inter-judge disagreement vs secondary judge
3. Position / format sensitivity probes
4. Score distribution shift on anchor set

If `JudgeCalibrationReport.action` is `shadow_only` or `disable`, EvalAgent's output `judge_health.healthy_for_routing_decision` is set to `False`. Downstream RoutingDecisionAgent downgrades `routing_confidence`; PolicyGate likely returns `require_human`.

## Open items for next iteration
- Define `RubricDefinition` Pydantic schema (currently lives in docs/eval-drift-baseline.md as English description; needs structured form for tool calls).
- Decide whether EvalAgent runs per-rubric in parallel sub-calls or one combined call. Cost vs latency tradeoff. Default: combined call, parallelize only if `rubric_version` declares `parallel_required=True`.
