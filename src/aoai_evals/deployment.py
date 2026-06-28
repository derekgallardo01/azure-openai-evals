"""Azure OpenAI deployment manifest.

In Azure OpenAI, a *deployment* is a named instance of a model in a
specific region with a specific capacity and content filter configuration.
This file is what makes the kit Azure-specific (vs generic LLM evals):

- Multiple deployments can serve the same model (different regions for
  failover, different content filter profiles for different audiences).
- Each deployment has its own latency baseline, cost per 1K tokens
  (varies by region + model), and content filter blocklist behavior.
- Production readiness is per-deployment, not per-model.

The kit's eval runs against a deployment, not a raw model. That's what
catches "the prompt works on east-2 but the content filter on west-1
blocks it" before users hit it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# Approximate Azure OpenAI pricing per 1K tokens (USD), as of writing.
# These are rough; check the Azure pricing page for current numbers.
KNOWN_PRICING: dict[str, dict[str, float]] = {
    "gpt-4o":              {"input": 0.005,  "output": 0.015},
    "gpt-4o-mini":         {"input": 0.00015, "output": 0.0006},
    "gpt-4-turbo":         {"input": 0.01,   "output": 0.03},
    "gpt-35-turbo":        {"input": 0.0005, "output": 0.0015},
    "claude-3-5-sonnet":   {"input": 0.003,  "output": 0.015},  # Azure AI Foundry
}

# Approximate p50 latency baselines per model (ms for ~200-token output).
KNOWN_LATENCY_P50_MS: dict[str, int] = {
    "gpt-4o":            850,
    "gpt-4o-mini":       450,
    "gpt-4-turbo":      1100,
    "gpt-35-turbo":      350,
    "claude-3-5-sonnet": 950,
}


@dataclass
class ContentFilterConfig:
    """Azure OpenAI content filter levels per category.

    Allowed values per category: 'low', 'medium', 'high', 'off'.
    Higher = stricter. 'off' is only available to vetted customers.
    """
    hate: str = "medium"
    sexual: str = "medium"
    violence: str = "medium"
    self_harm: str = "medium"
    jailbreak_detection: bool = True
    protected_material_detection: bool = True

    def validate(self) -> list[str]:
        problems = []
        allowed = {"low", "medium", "high", "off"}
        for name in ("hate", "sexual", "violence", "self_harm"):
            v = getattr(self, name)
            if v not in allowed:
                problems.append(f"content_filter.{name}={v!r}, must be one of {sorted(allowed)}")
        return problems


@dataclass
class Deployment:
    """One Azure OpenAI deployment in one region.

    Field names match (more or less) what you'd see in the Azure portal
    or `az cognitiveservices account deployment` output - so a real
    deployment can be exported into this shape with minimal mapping.
    """
    name: str                        # the deployment name (caller specifies in API calls)
    model: str                       # underlying model id
    model_version: str               # e.g., "2024-08-06"
    region: str                      # e.g., "eastus", "westeurope"
    capacity_tpm: int                # tokens-per-minute quota
    content_filter: ContentFilterConfig
    sla_p95_latency_ms: int          # what the team committed to
    monthly_budget_usd: float        # cost cap, used by the cost-projection eval
    notes: str = ""

    def cost_estimate(self, input_tokens: int, output_tokens: int) -> float:
        """USD cost for one call at this deployment's model pricing."""
        pricing = KNOWN_PRICING.get(self.model, {"input": 0.0, "output": 0.0})
        return (input_tokens / 1000) * pricing["input"] + (output_tokens / 1000) * pricing["output"]

    def latency_baseline_ms(self) -> int:
        """Best-known p50 latency for this model (used as the eval baseline)."""
        return KNOWN_LATENCY_P50_MS.get(self.model, 1000)

    def validate(self) -> list[str]:
        problems: list[str] = []
        if not self.name:
            problems.append("Deployment name is empty.")
        if self.model not in KNOWN_PRICING:
            problems.append(f"Model '{self.model}' not in KNOWN_PRICING - cost projection won't be accurate.")
        if self.capacity_tpm <= 0:
            problems.append(f"capacity_tpm must be positive, got {self.capacity_tpm}.")
        if self.sla_p95_latency_ms <= 0:
            problems.append(f"sla_p95_latency_ms must be positive, got {self.sla_p95_latency_ms}.")
        if self.monthly_budget_usd <= 0:
            problems.append(f"monthly_budget_usd must be positive, got {self.monthly_budget_usd}.")
        problems.extend(self.content_filter.validate())
        return problems

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Deployment":
        cf_raw = data.get("content_filter") or {}
        return cls(
            name=data["name"],
            model=data["model"],
            model_version=data.get("model_version", "latest"),
            region=data["region"],
            capacity_tpm=int(data.get("capacity_tpm", 30000)),
            content_filter=ContentFilterConfig(**cf_raw),
            sla_p95_latency_ms=int(data.get("sla_p95_latency_ms", 2000)),
            monthly_budget_usd=float(data.get("monthly_budget_usd", 100.0)),
            notes=data.get("notes", ""),
        )


# ----- Disk layout -----------------------------------------------------------

def load_deployment(path: Path | str) -> Deployment:
    p = Path(path)
    with open(p) as f:
        data = json.load(f)
    return Deployment.from_dict(data)


def list_deployments(root: Path | str) -> list[Deployment]:
    """Load every <name>.json file in the directory as a Deployment."""
    root = Path(root)
    return sorted(
        (load_deployment(p) for p in root.glob("*.json")),
        key=lambda d: d.name,
    )


def default_deployments_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "deployments"
