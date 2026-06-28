# Architecture

The kit is **Azure-specific** in three places:

1. **Deployment manifest** has Azure-particular fields
   (`region`, `capacity_tpm`, full `ContentFilterConfig`).
2. **Stub backend** simulates Azure's response shape including
   content-filter triggers, jailbreak detection, and per-model
   latency baselines.
3. **Rubric set** includes Azure-specific checks
   (`content_filter_safe`, `content_filter_blocks`) alongside generic
   text rubrics.

Everything else (file layout, eval cases JSON, CLI shape, test
patterns) follows the same conventions as
[prompt-registry-kit](https://github.com/derekgallardo01/prompt-registry-kit)
and [document-classifier-kit](https://github.com/derekgallardo01/document-classifier-kit).

## Three layers

```
deployments/X.json          (declarative deployment config)
        ↓
    Deployment.from_dict()   (typed config + validation)
        ↓
    Runner.run(d, prompt)    (executes against stub or live Azure)
        ↓
    evaluate_case(case, run, deployment)
        ↓
    EvalReport (pass/fail per case)
```

## The deployment manifest

[src/aoai_evals/deployment.py](../src/aoai_evals/deployment.py)
exports `Deployment` and `ContentFilterConfig`. Each deployment
declares everything that matters for production readiness:

| Field | Why it's per-deployment |
|---|---|
| `name` | Used by the Azure client to address the deployment in API calls |
| `model` + `model_version` | Same model can be deployed at multiple versions; per-version pricing + latency varies |
| `region` | Latency varies by region; quota is per-region |
| `capacity_tpm` | Per-deployment throughput cap; the eval can warn before you hit it |
| `content_filter` | 4 categories × 4 levels + jailbreak/protected-material toggles; per-deployment because different audiences need different policies |
| `sla_p95_latency_ms` | The SLA you committed to per-deployment, not per-model |
| `monthly_budget_usd` | Per-deployment budget cap |

`Deployment.validate()` is a static check. Run via
`aoai-evals validate <name>` or programmatically before promote.

## Pricing + latency tables

`KNOWN_PRICING` and `KNOWN_LATENCY_P50_MS` in `deployment.py` are
**approximate** baselines for the cost projection and stub latency.
Check the Azure pricing page for current numbers.

For real production cost tracking, capture the `usage` field from
each Azure response — the kit doesn't replace per-call observability,
it gives you a **projection** ahead of deployment.

## The stub backend

The stub simulates Azure OpenAI's response shape so the eval suite
runs without an Azure subscription. It:

- **Hashes the input** for deterministic responses (same prompt → same
  output → same latency every run).
- **Estimates token counts** from text length (4 chars/token).
- **Triggers content-filter blocks** when the prompt contains specific
  words (`"ignore previous instructions"`, `"build a weapon"`, etc.).
  Configurable per-deployment via the content filter levels — setting
  a category to `"off"` disables its triggers.
- **Generates synthetic latency** as model_baseline ± 20% deterministic
  jitter.

This is enough for the eval suite to verify **the gate logic itself**.
For real latency / cost / content-filter numbers, wire the Azure
backend and run against your live deployment.

## The runner's seam

```python
def run(self, deployment, prompt, expected_output_tokens=200):
    if self.backend == "azure":
        return self._call_azure(deployment, prompt, expected_output_tokens)
    return self._call_stub(deployment, prompt, expected_output_tokens)
```

Both return a `RunResult` with the same shape:

```python
RunResult(
    deployment_name="gpt4o-eastus-prod",
    model="gpt-4o",
    region="eastus",
    prompt="...",
    response_text="...",
    input_tokens=42,
    output_tokens=160,
    latency_ms=935,
    content_filter=ContentFilterResult(blocked=False, triggered_categories=[]),
    estimated_cost_usd=0.0024,
    backend="stub",
    error=None,
)
```

The evaluator only ever sees `RunResult` — it doesn't know or care
which backend produced it.

## The rubrics

Six rubric types in `evaluator.py::evaluate_case`. Three are
Azure-specific, three are generic:

**Azure-specific:**
- `content_filter_safe` — response NOT blocked (negative-case eval)
- `content_filter_blocks` — response IS blocked (positive-case eval)
- `latency_under: N` — `run.latency_ms <= N`
- `cost_under_per_call: $X` — `run.estimated_cost_usd <= X`
- `json_schema_conforms: {required: [...]}` — `json.loads(response)`
  has the required keys

**Generic:**
- `contains_all: [...]` — every substring in response
- `contains_any: [...]` — at least one substring
- `in_set: [...]` — response matches one of the allowed values

Add a custom rubric type by extending `evaluate_case`. Pattern:

```python
if rubric_type == "your_new_check":
    threshold = rubric["your_new_check"]
    ok = <your assertion>
    return CaseResult(case_id=case["id"], passed=ok, expected=rubric,
                      detail="OK" if ok else f"Failure: {...}")
```

## Cost projection (separate from rubrics)

`project_monthly_cost(deployment, monthly_calls, avg_input, avg_output)`
returns a `CostProjection` with the projected monthly USD and an
`over_budget` flag.

This is a CLI command, not a rubric — you call it ad-hoc with your
expected volume, not as part of the per-case eval suite. Use it to:

- Pick the right model for the volume (`gpt-4o-mini` vs `gpt-4o`)
- Validate that switching to a more expensive model still fits budget
- Catch the "we changed the prompt and now it's 4x longer" cost regression

## Latency baseline (multi-sample)

`measure_latency(deployment, prompt, samples=N)` runs the same prompt
N times, computes p50/p95/p99, compares to the deployment's SLA.

For real Azure latency measurement, you want N=100+ (the kit defaults
to 10 because the stub is deterministic and more samples don't add
signal). When you wire the Azure backend, bump samples up.

## Why a stub at all?

Three reasons:

1. **No Azure subscription needed.** Cloning + running takes 60
   seconds; no Azure tenant, no OpenAI resource, no quota approval.
2. **Deterministic CI.** Real Azure latency varies by minute; cost
   varies by token count which varies by response; content-filter
   behavior shifts across Azure updates. The stub eliminates all
   three so CI can gate on exact assertions.
3. **The eval framework itself is the value.** Wire to real Azure
   when you're ready; the eval shape, the manifest format, the rubric
   types, the cost-projection math — those are the work, not the
   thing pretending to be an LLM.

## What's deliberately NOT in the kit

- **Real Azure deployment provisioning** — that's the `az` CLI or
  Bicep templates. The kit reads existing deployment configs (which
  you'd typically export from Azure or maintain in version control
  alongside your Bicep).
- **Production observability** — capture `usage` from real Azure
  calls and pipe to Application Insights / Datadog / etc. The kit
  is the **pre-deployment** gate, not the runtime observability layer.
- **Per-request retry / failover logic** — that's your client wrapper
  in front of the deployment. The kit tells you which deployments
  are ready; your wrapper decides which one to send each request to.
- **Multi-tenant prompt routing** — out of scope. Add a routing layer
  upstream that picks which deployment + prompt to use for each
  tenant.
