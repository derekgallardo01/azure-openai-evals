"""Tests for the Deployment + ContentFilterConfig + Catalog loading."""

import json
import sys
import os
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402
from aoai_evals.deployment import (  # noqa: E402
    ContentFilterConfig, Deployment, list_deployments,
    load_deployment, default_deployments_dir, KNOWN_PRICING,
)


BUNDLED = default_deployments_dir()


def test_bundled_deployments_present():
    deployments = list_deployments(BUNDLED)
    names = {d.name for d in deployments}
    assert "gpt4o-eastus-prod" in names
    assert "gpt4o-westeurope-failover" in names
    assert "gpt4o-mini-bulk" in names


def test_load_deployment_parses_content_filter():
    d = load_deployment(BUNDLED / "gpt4o-eastus-prod.json")
    assert d.content_filter.hate == "medium"
    assert d.content_filter.jailbreak_detection is True


def test_validate_passes_on_well_formed_deployment():
    d = load_deployment(BUNDLED / "gpt4o-eastus-prod.json")
    assert d.validate() == []


def test_validate_catches_negative_capacity():
    d = Deployment(
        name="bad", model="gpt-4o", model_version="x", region="eastus",
        capacity_tpm=-1,
        content_filter=ContentFilterConfig(),
        sla_p95_latency_ms=1000, monthly_budget_usd=100.0,
    )
    problems = d.validate()
    assert any("capacity_tpm" in p for p in problems)


def test_validate_catches_invalid_content_filter_level():
    d = Deployment(
        name="bad", model="gpt-4o", model_version="x", region="eastus",
        capacity_tpm=1000,
        content_filter=ContentFilterConfig(hate="ridiculous"),
        sla_p95_latency_ms=1000, monthly_budget_usd=100.0,
    )
    problems = d.validate()
    assert any("hate" in p and "ridiculous" in p for p in problems)


def test_validate_flags_unknown_model():
    d = Deployment(
        name="exp", model="gpt-7-unicorn", model_version="x", region="eastus",
        capacity_tpm=1000,
        content_filter=ContentFilterConfig(),
        sla_p95_latency_ms=1000, monthly_budget_usd=100.0,
    )
    problems = d.validate()
    assert any("not in KNOWN_PRICING" in p for p in problems)


def test_cost_estimate_matches_known_pricing_table():
    d = load_deployment(BUNDLED / "gpt4o-eastus-prod.json")
    # 1000 input tokens at gpt-4o = 1 * 0.005 = 0.005
    # 1000 output tokens at gpt-4o = 1 * 0.015 = 0.015
    assert d.cost_estimate(1000, 1000) == pytest.approx(0.020, rel=0.001)


def test_cost_estimate_for_zero_tokens():
    d = load_deployment(BUNDLED / "gpt4o-eastus-prod.json")
    assert d.cost_estimate(0, 0) == 0.0


def test_latency_baseline_has_value_for_known_model():
    d = load_deployment(BUNDLED / "gpt4o-eastus-prod.json")
    assert d.latency_baseline_ms() > 0


def test_failover_deployment_has_lower_capacity_than_primary():
    primary = load_deployment(BUNDLED / "gpt4o-eastus-prod.json")
    failover = load_deployment(BUNDLED / "gpt4o-westeurope-failover.json")
    assert failover.capacity_tpm < primary.capacity_tpm
