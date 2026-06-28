"""Tests for the evaluator + cost projection + latency baseline."""

import json
import sys
import os
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from aoai_evals.deployment import (  # noqa: E402
    Deployment, ContentFilterConfig, load_deployment, default_deployments_dir,
)
from aoai_evals.evaluator import (  # noqa: E402
    evaluate_case, evaluate_deployment, project_monthly_cost, measure_latency,
)
from aoai_evals.runner import RunResult, ContentFilterResult, Runner  # noqa: E402


BUNDLED = default_deployments_dir()


def _run(response_text="ok", input_tokens=10, output_tokens=10,
         latency_ms=500, cost_usd=0.001, blocked=False, categories=None, error=None):
    return RunResult(
        deployment_name="x", model="gpt-4o", region="eastus", prompt="p",
        response_text=response_text,
        input_tokens=input_tokens, output_tokens=output_tokens,
        latency_ms=latency_ms, estimated_cost_usd=cost_usd,
        content_filter=ContentFilterResult(blocked=blocked, triggered_categories=categories or []),
        backend="stub", error=error,
    )


def _depl():
    return load_deployment(BUNDLED / "gpt4o-eastus-prod.json")


# ---------- Per-rubric scoring ---------------------------------------------

def test_content_filter_safe_passes_when_not_blocked():
    case = {"id": "x", "expected": {"content_filter_safe": True}}
    r = evaluate_case(case, _run(blocked=False), _depl())
    assert r.passed is True


def test_content_filter_safe_fails_when_blocked():
    case = {"id": "x", "expected": {"content_filter_safe": True}}
    r = evaluate_case(case, _run(blocked=True, categories=["hate"]), _depl())
    assert r.passed is False
    assert "Blocked" in r.detail
    assert "hate" in r.detail


def test_content_filter_blocks_passes_when_blocked():
    case = {"id": "x", "expected": {"content_filter_blocks": True}}
    r = evaluate_case(case, _run(blocked=True), _depl())
    assert r.passed is True


def test_content_filter_blocks_fails_when_not_blocked():
    case = {"id": "x", "expected": {"content_filter_blocks": True}}
    r = evaluate_case(case, _run(blocked=False), _depl())
    assert r.passed is False


def test_latency_under_passes_under_threshold():
    case = {"id": "x", "expected": {"latency_under": 1000}}
    r = evaluate_case(case, _run(latency_ms=500), _depl())
    assert r.passed is True


def test_latency_under_fails_over_threshold():
    case = {"id": "x", "expected": {"latency_under": 500}}
    r = evaluate_case(case, _run(latency_ms=900), _depl())
    assert r.passed is False
    assert "900ms" in r.detail


def test_cost_under_per_call_passes_under_budget():
    case = {"id": "x", "expected": {"cost_under_per_call": 0.01}}
    r = evaluate_case(case, _run(cost_usd=0.005), _depl())
    assert r.passed is True


def test_cost_under_per_call_fails_over_budget():
    case = {"id": "x", "expected": {"cost_under_per_call": 0.001}}
    r = evaluate_case(case, _run(cost_usd=0.01), _depl())
    assert r.passed is False


def test_json_schema_conforms_with_valid_json():
    case = {"id": "x", "expected": {"json_schema_conforms": {"required": ["name", "age"]}}}
    r = evaluate_case(case, _run(response_text='{"name": "alice", "age": 30}'), _depl())
    assert r.passed is True


def test_json_schema_conforms_fails_on_invalid_json():
    case = {"id": "x", "expected": {"json_schema_conforms": {"required": ["name"]}}}
    r = evaluate_case(case, _run(response_text="not json"), _depl())
    assert r.passed is False
    assert "not valid JSON" in r.detail


def test_json_schema_conforms_fails_on_missing_required():
    case = {"id": "x", "expected": {"json_schema_conforms": {"required": ["name", "missing"]}}}
    r = evaluate_case(case, _run(response_text='{"name": "alice"}'), _depl())
    assert r.passed is False
    assert "missing" in r.detail


def test_runner_error_propagates_as_failure():
    case = {"id": "x", "expected": {"contains_all": ["anything"]}}
    r = evaluate_case(case, _run(error="boom"), _depl())
    assert r.passed is False
    assert "Runner error" in r.detail


# ---------- End-to-end against bundled deployments + cases ------------------

def test_bundled_eastus_eval_passes_in_full():
    d = load_deployment(BUNDLED / "gpt4o-eastus-prod.json")
    with open(Path(__file__).resolve().parents[1] / "evals" / "gpt4o-eastus-prod.json") as f:
        cases = json.load(f)["cases"]
    report = evaluate_deployment(d, cases)
    assert report.passed == report.total, [c for c in report.cases if not c.passed]


def test_bundled_mini_bulk_eval_passes_in_full():
    d = load_deployment(BUNDLED / "gpt4o-mini-bulk.json")
    with open(Path(__file__).resolve().parents[1] / "evals" / "gpt4o-mini-bulk.json") as f:
        cases = json.load(f)["cases"]
    report = evaluate_deployment(d, cases)
    assert report.passed == report.total


# ---------- Cost projection -------------------------------------------------

def test_cost_projection_under_budget():
    d = load_deployment(BUNDLED / "gpt4o-mini-bulk.json")
    proj = project_monthly_cost(d, monthly_calls=100_000,
                                 avg_input_tokens=200, avg_output_tokens=30)
    assert proj.over_budget is False
    assert proj.monthly_cost_usd > 0


def test_cost_projection_flags_over_budget():
    d = load_deployment(BUNDLED / "gpt4o-mini-bulk.json")
    # 10M calls at 1000 in / 500 out tokens will blow the $100 budget
    proj = project_monthly_cost(d, monthly_calls=10_000_000,
                                 avg_input_tokens=1000, avg_output_tokens=500)
    assert proj.over_budget is True


# ---------- Latency baseline ------------------------------------------------

def test_measure_latency_returns_sorted_percentiles():
    d = load_deployment(BUNDLED / "gpt4o-eastus-prod.json")
    report = measure_latency(d, "Hello", samples=5)
    assert report.samples == 5
    assert report.p50_ms <= report.p95_ms <= report.p99_ms


def test_measure_latency_compares_to_sla():
    d = load_deployment(BUNDLED / "gpt4o-eastus-prod.json")
    report = measure_latency(d, "Hello", samples=5)
    # The stub's gpt-4o latency baseline is 850ms; SLA is 1500ms; should pass.
    assert report.p95_under_sla is True
