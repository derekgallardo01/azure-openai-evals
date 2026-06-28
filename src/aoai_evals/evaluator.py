"""Eval rubrics tuned to Azure OpenAI specifics.

These are the checks worth running against a deployment before promoting
it to production:

- content_filter_safe: prompts that SHOULD work don't trigger the filter
- content_filter_blocks: prompts that SHOULD be blocked are blocked
  (catches misconfigured content_filter levels)
- latency_under: p95 latency under deployment's SLA
- cost_under_per_call: per-call cost under budget
- monthly_cost_projection: projected monthly cost at expected volume under budget
- json_schema_conforms: structured output matches declared schema
- contains_all / contains_any: standard text-rubric checks
"""

from __future__ import annotations

import json
import re
import statistics
from dataclasses import dataclass, field
from typing import Any

from .deployment import Deployment
from .runner import RunResult, Runner


@dataclass
class CaseResult:
    case_id: str
    passed: bool
    expected: dict[str, Any]
    detail: str  # human-readable reason


@dataclass
class EvalReport:
    deployment_name: str
    cases: list[CaseResult]

    @property
    def passed(self) -> int:
        return sum(1 for c in self.cases if c.passed)

    @property
    def total(self) -> int:
        return len(self.cases)

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0


def evaluate_case(case: dict, run: RunResult, deployment: Deployment) -> CaseResult:
    rubric = case.get("expected") or {}
    rubric_type = next(iter(rubric)) if rubric else None

    if run.error:
        return CaseResult(case_id=case["id"], passed=False, expected=rubric,
                          detail=f"Runner error: {run.error}")

    # ----- Azure-specific rubrics ---------------------------------------

    if rubric_type == "content_filter_safe":
        # Response should NOT be blocked by content filter.
        ok = not run.content_filter.blocked
        detail = "OK" if ok else f"Blocked by content filter (categories: {run.content_filter.triggered_categories})"
        return CaseResult(case_id=case["id"], passed=ok, expected=rubric, detail=detail)

    if rubric_type == "content_filter_blocks":
        # Response SHOULD be blocked (e.g., jailbreak attempt).
        ok = run.content_filter.blocked
        detail = "OK - blocked" if ok else "Should have been blocked but went through"
        return CaseResult(case_id=case["id"], passed=ok, expected=rubric, detail=detail)

    if rubric_type == "latency_under":
        threshold_ms = int(rubric["latency_under"])
        ok = run.latency_ms <= threshold_ms
        detail = f"{run.latency_ms}ms vs threshold {threshold_ms}ms"
        return CaseResult(case_id=case["id"], passed=ok, expected=rubric, detail=detail)

    if rubric_type == "cost_under_per_call":
        threshold = float(rubric["cost_under_per_call"])
        ok = run.estimated_cost_usd <= threshold
        detail = f"${run.estimated_cost_usd:.5f} vs threshold ${threshold:.5f}"
        return CaseResult(case_id=case["id"], passed=ok, expected=rubric, detail=detail)

    if rubric_type == "json_schema_conforms":
        schema = rubric["json_schema_conforms"]
        try:
            parsed = json.loads(run.response_text)
        except json.JSONDecodeError as ex:
            return CaseResult(case_id=case["id"], passed=False, expected=rubric,
                              detail=f"Response is not valid JSON: {ex}")
        missing = [k for k in schema.get("required", []) if k not in parsed]
        if missing:
            return CaseResult(case_id=case["id"], passed=False, expected=rubric,
                              detail=f"Missing required keys: {missing}")
        return CaseResult(case_id=case["id"], passed=True, expected=rubric,
                          detail=f"All required keys present: {schema.get('required', [])}")

    # ----- Generic text rubrics -----------------------------------------

    if rubric_type == "contains_all":
        substrings = rubric["contains_all"]
        missing = [s for s in substrings if s.lower() not in run.response_text.lower()]
        ok = not missing
        detail = "OK" if ok else f"Missing required: {missing}"
        return CaseResult(case_id=case["id"], passed=ok, expected=rubric, detail=detail)

    if rubric_type == "contains_any":
        substrings = rubric["contains_any"]
        ok = any(s.lower() in run.response_text.lower() for s in substrings)
        detail = "OK" if ok else f"None of {substrings} present"
        return CaseResult(case_id=case["id"], passed=ok, expected=rubric, detail=detail)

    if rubric_type == "in_set":
        allowed = set(rubric["in_set"])
        ok = run.response_text.strip() in allowed
        detail = "OK" if ok else f"Response not in allowed set: {sorted(allowed)}"
        return CaseResult(case_id=case["id"], passed=ok, expected=rubric, detail=detail)

    return CaseResult(case_id=case["id"], passed=False, expected=rubric,
                      detail=f"Unknown rubric type: {rubric_type}")


def evaluate_deployment(deployment: Deployment, cases: list[dict],
                        runner: Runner | None = None) -> EvalReport:
    runner = runner or Runner()
    results: list[CaseResult] = []
    for case in cases:
        prompt = case["prompt"]
        expected_out = int(case.get("expected_output_tokens", 200))
        run = runner.run(deployment, prompt, expected_output_tokens=expected_out)
        results.append(evaluate_case(case, run, deployment))
    return EvalReport(deployment_name=deployment.name, cases=results)


# ----- Cost projection (separate from the per-case rubrics) ------------------

@dataclass
class CostProjection:
    """Monthly cost projection at a given call volume."""
    deployment_name: str
    model: str
    monthly_calls: int
    avg_input_tokens: int
    avg_output_tokens: int
    monthly_cost_usd: float
    budget_usd: float
    over_budget: bool


def project_monthly_cost(deployment: Deployment, monthly_calls: int,
                         avg_input_tokens: int, avg_output_tokens: int) -> CostProjection:
    per_call = deployment.cost_estimate(avg_input_tokens, avg_output_tokens)
    monthly = per_call * monthly_calls
    return CostProjection(
        deployment_name=deployment.name,
        model=deployment.model,
        monthly_calls=monthly_calls,
        avg_input_tokens=avg_input_tokens,
        avg_output_tokens=avg_output_tokens,
        monthly_cost_usd=round(monthly, 2),
        budget_usd=deployment.monthly_budget_usd,
        over_budget=monthly > deployment.monthly_budget_usd,
    )


# ----- Latency baseline (multi-sample) --------------------------------------

@dataclass
class LatencyReport:
    deployment_name: str
    samples: int
    p50_ms: int
    p95_ms: int
    p99_ms: int
    sla_p95_ms: int
    p95_under_sla: bool


def measure_latency(deployment: Deployment, prompt: str, samples: int = 10,
                    runner: Runner | None = None) -> LatencyReport:
    """Run the prompt N times, compute latency percentiles, compare to SLA."""
    runner = runner or Runner()
    latencies = [runner.run(deployment, prompt).latency_ms for _ in range(samples)]
    latencies.sort()
    p50 = int(statistics.median(latencies))
    p95 = latencies[max(0, int(0.95 * len(latencies)) - 1)]
    p99 = latencies[max(0, int(0.99 * len(latencies)) - 1)]
    return LatencyReport(
        deployment_name=deployment.name,
        samples=samples, p50_ms=p50, p95_ms=p95, p99_ms=p99,
        sla_p95_ms=deployment.sla_p95_latency_ms,
        p95_under_sla=p95 <= deployment.sla_p95_latency_ms,
    )
