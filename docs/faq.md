# FAQ

## How is this different from `prompt-registry-kit`?

`prompt-registry-kit` is **generic LLM prompt management** —
versioning, A/B variants, eval-gated promotion. Works with any LLM
provider.

This kit is **Azure-specific deployment readiness** — checks the
Azure-particular concerns (content filter triggers, per-deployment
SLA + budget, regional latency, JSON-schema conformance for Azure's
JSON mode).

They compose. Manage prompt versions in `prompt-registry-kit`;
evaluate the active prompts against your Azure deployments with this
kit.

## How is this different from Azure AI Studio's built-in evaluations?

Azure AI Studio has a hosted evaluation feature. It's:
- A UI in the Azure portal
- Tied to your Azure subscription + Foundry workspace
- Per-evaluation billing
- Stores results in the workspace

This kit is:
- A CLI + Python library in your repo
- No Azure subscription needed for the development loop (stub backend)
- Zero per-run cost
- Stores results wherever you want (git, sqlite, your existing
  observability stack)

Both have a place. Use Azure AI Studio's evaluations for one-off
exploration; use this kit for the **pre-deployment gate that runs in
CI on every PR**.

## How is this different from promptfoo / promptflow / DeepEval?

Those are **generic LLM eval frameworks** — they support multiple
providers and have broad rubric libraries.

This kit is **focused on Azure OpenAI's specifics** — content filter
behavior, per-deployment manifest with region + capacity + SLA, JSON
mode conformance for the Azure JSON output flag.

If you're multi-cloud or platform-agnostic, use promptfoo. If you're
on Azure OpenAI and want a kit that knows about your `eastus` vs
`westeurope` deployment differences, use this one. (Or both — they
don't conflict.)

## Why a stub backend if the kit is about Azure?

Three reasons:

1. **No subscription needed for development.** Clone the kit, run
   `aoai-evals demo` in 60 seconds. New engineer onboards without
   provisioning Azure access first.
2. **Deterministic CI.** Real Azure latency varies; real cost varies;
   content filter behavior shifts across Azure updates. The stub makes
   the kit's eval framework itself testable in isolation.
3. **The framework is the value.** Once you wire `_call_azure`, the
   rubrics + manifests + cost projections + readiness gate are what
   you'd be building anyway. The stub is the on-ramp, not the
   destination.

## Will the cost projections match Azure's actual billing?

Approximately. The `KNOWN_PRICING` table tracks Azure's public per-1K
token pricing. Reality differs by:

- **Region surcharges** — some regions charge ~10% more
- **Committed-use discounts** — if you've negotiated a discount with
  Microsoft, your effective price is lower
- **Provisioned throughput** — PTU pricing is fixed monthly, not
  per-token
- **Tokenization** — input length in chars is an approximation; the
  real tokenizer (tiktoken) gives more accurate counts

For directional planning, the kit's projections are fine. For actual
budget commitments, capture real `usage` from production responses
and compute from that.

## What about Provisioned Throughput Units (PTU)?

PTU is a different pricing model — you commit to a monthly capacity
and pay flat instead of per-token. The kit's `cost_under_per_call`
rubric doesn't make sense for PTU deployments.

For PTU deployments, override the cost projection:

```python
def project_ptu_cost(deployment, ptu_count, ptu_monthly_rate):
    return ptu_count * ptu_monthly_rate
```

And mark per-call cost as N/A in the rubrics for that deployment.

## How does the kit handle content filter overrides (vetted customers)?

Microsoft lets vetted customers set content filter levels to `"off"`
for some categories. The kit honors that:

- `ContentFilterConfig.validate()` accepts `"off"` as a valid level.
- The stub backend's trigger words for a category only fire if that
  category is enabled (not `"off"`).

In production with `_call_azure` wired, Azure's response will reflect
whatever filter the deployment actually has configured — your kit's
config is just the spec, not the enforcement.

## What's the right SLA to commit to per deployment?

Rule of thumb based on the bundled defaults:

- **gpt-4o** in same-region: p95 ≈ 1500ms
- **gpt-4o** cross-region: p95 ≈ 1800ms
- **gpt-4o-mini** anywhere: p95 ≈ 800ms
- **gpt-4-turbo** same-region: p95 ≈ 2000ms

Add 20% margin for noise → safe SLA commitments. Real numbers vary
by region capacity and time of day; measure your specific deployment
with `aoai-evals latency --samples 100` before committing.

## Does the kit handle Azure AI Foundry (Claude, Llama, Mistral, etc.)?

The current shape is Azure OpenAI specific. For Foundry models:

1. Add their pricing to `KNOWN_PRICING`
2. Add their latency baselines to `KNOWN_LATENCY_P50_MS`
3. Point `_call_azure` at the Foundry endpoint instead of Azure OpenAI

The content-filter rubrics still apply (Foundry inherits Azure's
content filter). The cost / latency math is the same shape, just
different numbers.

## Can I gate Bicep / Terraform deployments on this?

Yes — call `aoai-evals validate <deployment>` from a pre-apply hook:

```hcl
# Terraform pre-apply
resource "null_resource" "aoai_validate" {
  provisioner "local-exec" {
    command = "aoai-evals validate ${azurerm_cognitive_deployment.this.name}"
  }
  depends_on = [local_file.deployment_manifest]
}
```

The manifest needs to be written to disk before validate runs. Add a
script that generates `deployments/<name>.json` from Terraform state.

## What's the right cadence to re-run the eval suite?

- **On every PR that changes anything in `deployments/`, `evals/`, or
  `prompts/`** — catches regressions before merge.
- **Nightly cron against real Azure** — catches Azure-side drift
  (content filter updates, pricing changes, regional latency
  regressions).
- **On every Azure model version upgrade** — Microsoft sometimes
  ships model updates that change JSON-mode reliability or
  content-filter behavior; the eval suite tells you what changed.

The bundled CI workflow handles the first. Add a scheduled workflow
for the second + third.

## Can the kit help me decide between gpt-4o and gpt-4o-mini?

Yes:

1. Write the same eval cases against both deployments
   (`evals/gpt4o-eastus-prod.json` + `evals/gpt4o-mini-eastus-prod.json`).
2. Run both: `aoai-evals eval gpt4o-eastus-prod` and
   `aoai-evals eval gpt4o-mini-eastus-prod`.
3. Compare pass rates. If gpt-4o-mini also passes, switch — it's
   ~50x cheaper.
4. Run `aoai-evals cost` for both at your expected volume to see the
   savings.

This is the "model rightsizing" workflow. Common ask in Azure
optimization engagements.
