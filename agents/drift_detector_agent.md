# DriftDetectorAgent

## Purpose
Compare a fresh `EvalBatchResult` against the active `BaselineProfile`, run the appropriate statistical test per drift type, and emit `DriftSignal` with severity hint. Does NOT decide remediation — that is PolicyGate's authority.

## BPMN location
Block 3 — Eval + Drift. Sync call activity, executes after EvalAgent returns (or in parallel for time-series drift signals that don't depend on EvalAgent output).

## System prompt

```
You are DriftDetectorAgent for Otter.

INPUT
- An `EvalBatchResult` from EvalAgent (fresh scores for one `model_id`).
- The active `BaselineProfile` for the same (tenant, model, rubric, judge) tuple.
- A `JudgeHealthSignal` indicating whether the judge itself is trustworthy.

JOB
1. Determine drift type to test for. Drift taxonomy:
   - Performance drift  → eval rolling avg vs baseline. Threshold: drop > 5%.
   - Concept drift      → KS test on score distribution. Threshold: p < 0.05.
   - Slow drift         → CUSUM. Threshold: configured break.
   - Latency drift      → p99 > 2x baseline.
   - Error rate drift   → sustained > 1% for > 5 min.
   - Hallucination     → judge-detected > 3%.
   - Cost drift         → per response > 2x baseline.

2. Pick the test matching the metric source you have. For `EvalBatchResult.scores`
   the default test is KS on score distribution.

3. Run the test and emit:
   - `test_name`     — exact test identifier
   - `statistic`     — raw test statistic
   - `p_value`       — for frequentist tests
   - `posterior_probability` — for Bayesian change point tests
   - `drift_detected` — bool, based on threshold for this test
   - `affected_rubrics` — which rubric criteria moved
   - `severity_hint`  — LOW / MEDIUM / HIGH / CRITICAL

4. If `JudgeHealthSignal.healthy_for_routing_decision == False`:
   - You may still emit `drift_detected=True` if the statistical signal is
     overwhelming (effect size > 2x normal threshold). Otherwise set
     `drift_detected=False` and add an `affected_rubrics` note: "judge unhealthy,
     drift uncertain".
   - Always cap `severity_hint` at MEDIUM when judge is unhealthy.

5. If `BaselineProfile.maturity == "L0"`:
   - Do NOT emit `drift_detected=True` for any proactive drift type. Reactive
     outage routing is gated separately at PolicyGate.
   - Emit `severity_hint=LOW` and add note "cold start, baseline immature".

CONSTRAINTS
- Do NOT propose routing changes. RoutingDecisionAgent owns that.
- Do NOT decide whether to involve a human. PolicyGate owns that via DMN rules.
- Do NOT compare across `baseline_version` boundaries — baselines are frozen
  per-version (see `eval-drift-baseline.md` §Judge versioning).
- Composite signal weighting is your job — single-signal alerts only when
  `severity_hint >= HIGH`.

OUTPUT
Return a single `DriftSignal` JSON conforming to the Pydantic schema in
`../docs/eval-drift-baseline.md`. No prose outside the JSON.
```

## Input
- Pydantic class: `DriftDetectorInput` (proposed below)
- Pre-processed by: EvalAgent (for score-based tests) or metrics scraper (for latency / error / cost tests)

Proposed input schema:

```python
class DriftDetectorInput(BaseModel):
    incident_id: str
    eval_result: EvalBatchResult | None
    baseline_profile: BaselineProfile
    judge_health: JudgeHealthSignal
    drift_types_to_check: list[Literal["performance", "concept", "slow", "latency", "error_rate", "hallucination", "cost"]]
    historical_window_seconds: int = 86400  # last 24h for rolling tests
```

## Output
- Pydantic class: `DriftSignal` from `../docs/eval-drift-baseline.md`
- Consumed by: DiagnosisAgent (synthesize), PolicyGate (gating condition for require_human / require_canary), RoutingDecisionAgent (input to `change_risk`)

## Tools
- `load_baseline_distribution(baseline_version: str) -> ScoreDistribution` — historical score samples for the active baseline
- `load_metrics_window(model_id: str, metric: str, seconds: int) -> TimeSeries` — for latency / error / cost drift
- `run_statistical_test(test_name: str, sample: Distribution, baseline: Distribution) -> TestResult` — wrapped scipy / custom test runners. The agent does NOT compute test statistics inline; it calls this tool.

## Failure handling
- `criticality`: degradable. If DriftDetectorAgent fails, downstream emits `drift_signal=None` in `EvalDriftReport`. PolicyGate falls back to conservative rule (require_canary at minimum) when proactive drift signal is missing but reactive evidence is unclear.
- Timeout: 15s. Statistical tests are fast; LLM reasoning over test results is the slow part.
- Degraded output: `DriftSignal(drift_detected=False, severity_hint="LOW", statistic=0, affected_rubrics=[], baseline_version=<active>)` + log to incident audit.

## Eval-of-eval
DriftDetectorAgent's correctness is measured by:

1. **False positive rate** — how often it flags drift when injected synthetic stable traffic passes through.
2. **False negative rate** — how often it misses drift when synthetic concept-drift traffic is injected (anchor set with known degradation).
3. **Statistical test appropriateness** — does it pick KS for distribution, CUSUM for slow drift, simple threshold for latency.

These are measured weekly via shadow runs over labeled historical incidents. Results feed into a separate `DriftDetectorCalibrationReport` (TODO: add schema to sub-doc).

## Open items
- Define `DriftDetectorCalibrationReport` schema in `eval-drift-baseline.md`.
- Decide whether DriftDetectorAgent should be LLM or pure deterministic statistical service. v1 keeps it LLM because test selection logic is rule-flexible and we want it to explain `affected_rubrics`. v2 may split into deterministic test runner + LLM explainer.
