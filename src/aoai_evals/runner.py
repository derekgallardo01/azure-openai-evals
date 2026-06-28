"""Runner - sends a prompt to an Azure OpenAI deployment.

Default backend is a deterministic stub that simulates Azure OpenAI's
response shape (text, token counts, content_filter result, latency).
Set AOAI_BACKEND=azure to route through the real Azure OpenAI service.

The stub is designed to mimic the response surface the evaluator needs:
- Returns a text response (canned per-prompt)
- Reports input/output token counts (approximated from text length)
- Reports a content_filter result (some prompts deliberately trigger it
  so the content_filter eval has a positive case to test against)
- Returns a synthetic latency (model-baseline + jitter, deterministic
  per prompt hash so runs are reproducible)
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

from .deployment import Deployment


@dataclass
class ContentFilterResult:
    """What Azure OpenAI returns about content-filter checks."""
    blocked: bool
    triggered_categories: list[str] = field(default_factory=list)


@dataclass
class RunResult:
    """One invocation against an Azure OpenAI deployment."""
    deployment_name: str
    model: str
    region: str
    prompt: str
    response_text: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    content_filter: ContentFilterResult
    estimated_cost_usd: float
    backend: str
    error: str | None = None


class Runner:
    """Executes prompts against a deployment."""

    def __init__(self, backend: str | None = None):
        self.backend = backend or os.environ.get("AOAI_BACKEND", "stub")

    def run(self, deployment: Deployment, prompt: str,
            expected_output_tokens: int = 200) -> RunResult:
        t0 = time.perf_counter()
        try:
            if self.backend == "azure":
                response = self._call_azure(deployment, prompt, expected_output_tokens)
            else:
                response = self._call_stub(deployment, prompt, expected_output_tokens)
            error = None
        except Exception as ex:
            response = self._empty_response(deployment, prompt)
            error = str(ex)
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        # Use the simulated latency from the stub when available, otherwise the wall clock.
        actual_latency = response.latency_ms if response.latency_ms > 0 else elapsed_ms
        return RunResult(
            deployment_name=deployment.name,
            model=deployment.model,
            region=deployment.region,
            prompt=prompt,
            response_text=response.response_text,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            latency_ms=actual_latency,
            content_filter=response.content_filter,
            estimated_cost_usd=deployment.cost_estimate(
                response.input_tokens, response.output_tokens
            ),
            backend=self.backend,
            error=error,
        )

    # ----- The backend seam -----------------------------------------------

    def _call_stub(self, deployment: Deployment, prompt: str,
                   expected_output_tokens: int) -> "RunResult":
        """Deterministic stub that fakes the Azure OpenAI response shape.

        Uses a hash of (deployment + prompt) so two runs of the same input
        produce the same output - critical for the eval suite's
        reproducibility.
        """
        digest_int = int(hashlib.sha256(
            f"{deployment.name}|{prompt}".encode("utf-8")
        ).hexdigest()[:8], 16)

        # Input tokens: rough estimate at 4 chars/token.
        input_tokens = max(1, len(prompt) // 4)
        # Output tokens: ~80% of expected (LLMs typically come in shorter).
        output_tokens = max(1, int(expected_output_tokens * 0.8))

        # Synthetic latency: model baseline + jitter (deterministic, ±20%).
        baseline = deployment.latency_baseline_ms()
        jitter = (digest_int % 41) - 20  # -20..+20%
        latency = int(baseline * (1 + jitter / 100))

        # Content-filter simulation: trigger when prompt contains specific
        # words. This is what makes the content_filter eval testable.
        trigger_words = _content_filter_triggers(deployment.content_filter)
        triggered = [cat for cat, words in trigger_words.items()
                     if any(w in prompt.lower() for w in words)]
        blocked = len(triggered) > 0

        # If blocked, output is empty (matches Azure's actual behavior).
        if blocked:
            response_text = ""
            output_tokens = 0
        else:
            response_text = (f"[stub response for {deployment.name}@{deployment.model} | "
                             f"input-hash={digest_int:08x}]")

        return RunResult(
            deployment_name=deployment.name, model=deployment.model,
            region=deployment.region, prompt=prompt,
            response_text=response_text,
            input_tokens=input_tokens, output_tokens=output_tokens,
            latency_ms=latency,
            content_filter=ContentFilterResult(blocked=blocked, triggered_categories=triggered),
            estimated_cost_usd=0.0,  # filled in by run() so deployment is consistent
            backend=self.backend,
        )

    def _call_azure(self, deployment: Deployment, prompt: str,
                    expected_output_tokens: int) -> "RunResult":
        """Production swap point.

        Implementation sketch:

            from openai import AzureOpenAI
            client = AzureOpenAI(
                api_key=os.environ["AZURE_OPENAI_API_KEY"],
                api_version="2024-08-01-preview",
                azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            )
            response = client.chat.completions.create(
                model=deployment.name,  # NOTE: use deployment name, not model name
                messages=[{"role": "user", "content": prompt}],
                max_tokens=expected_output_tokens,
            )
            return RunResult(
                deployment_name=deployment.name, ...,
                response_text=response.choices[0].message.content,
                input_tokens=response.usage.prompt_tokens,
                output_tokens=response.usage.completion_tokens,
                latency_ms=0,  # let runner.run() compute from wall clock
                content_filter=ContentFilterResult(
                    blocked=response.choices[0].finish_reason == "content_filter",
                    triggered_categories=_parse_content_filter(response),
                ),
                ...
            )

        Until wired, fall back to stub so the kit still runs.
        """
        return self._call_stub(deployment, prompt, expected_output_tokens)

    def _empty_response(self, deployment: Deployment, prompt: str) -> "RunResult":
        return RunResult(
            deployment_name=deployment.name, model=deployment.model,
            region=deployment.region, prompt=prompt, response_text="",
            input_tokens=0, output_tokens=0, latency_ms=0,
            content_filter=ContentFilterResult(blocked=False),
            estimated_cost_usd=0.0, backend=self.backend,
        )


# ----- Content-filter trigger simulation -------------------------------------

def _content_filter_triggers(cf) -> dict[str, list[str]]:
    """Words that trigger each content-filter category, weighted by strictness.

    Only used by the stub. In production the actual Azure content filter
    runs server-side and the trigger words don't matter at the kit level.
    """
    triggers: dict[str, list[str]] = {}
    if cf.hate != "off":
        triggers["hate"] = ["i hate you", "they're all"]
    if cf.violence != "off":
        triggers["violence"] = ["build a weapon", "how to attack"]
    if cf.self_harm != "off":
        triggers["self_harm"] = ["hurt myself"]
    if cf.sexual != "off":
        triggers["sexual"] = ["explicit"]
    if cf.jailbreak_detection:
        triggers["jailbreak"] = ["ignore previous instructions",
                                  "you are now in dev mode"]
    return triggers
