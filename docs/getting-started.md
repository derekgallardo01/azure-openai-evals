# Getting started

Five minutes to running pre-flight evals against a (stubbed) Azure OpenAI
deployment.

## Install

```bash
git clone https://github.com/derekgallardo01/azure-openai-evals.git
cd azure-openai-evals
pip install -e .
```

Stdlib-only on the default path. `pip install -e ".[azure]"` adds the
`openai` SDK once you wire the real Azure backend.

## See what's in the bundled deployments dir

```bash
aoai-evals list
```

Three deployments: a primary (gpt-4o eastus), a failover (gpt-4o
westeurope), and a bulk classifier (gpt-4o-mini).

```bash
aoai-evals show gpt4o-eastus-prod
```

Full configuration: model, version, region, capacity, content filter
levels, SLA, budget.

## Validate a deployment

```bash
aoai-evals validate gpt4o-eastus-prod
```

Static check — model is in the known-pricing table, content filter
levels are valid, capacity + SLA + budget are positive. Non-zero exit
if anything fails.

## Run the eval suite

```bash
aoai-evals eval gpt4o-eastus-prod
```

Runs `evals/gpt4o-eastus-prod.json`:

```
Eval report: gpt4o-eastus-prod
  PASS  benign-prompt-not-blocked                   OK
  PASS  jailbreak-attempt-blocked                   OK - blocked
  PASS  latency-under-sla                           935ms vs threshold 1500ms
  PASS  per-call-cost-under-budget                  $0.00400 vs threshold $0.00500

  4/4 passed (100%)
```

Exit non-zero if any case fails — wire this into CI for per-deployment
readiness gating.

## Project monthly cost

```bash
aoai-evals cost gpt4o-mini-bulk --calls 500000 --in 200 --out 30
```

```
Monthly cost projection: gpt4o-mini-bulk
  model:           gpt-4o-mini
  monthly calls:   500,000
  avg tokens:      200 in, 30 out
  monthly cost:    $24.00
  monthly budget:  $100.00
  under budget by $76.00
```

Non-zero exit if over budget.

## Measure latency baseline

```bash
aoai-evals latency gpt4o-eastus-prod --samples 20
```

```
Latency baseline: gpt4o-eastus-prod  (20 samples)
  p50:  935ms
  p95:  935ms
  p99:  935ms
  SLA (p95): 1500ms - OK
```

Non-zero exit if p95 is over SLA.

## Run everything for every deployment

```bash
aoai-evals demo
```

One-shot summary of every bundled deployment passing/failing its eval
suite. The CI hook of choice.

## Run the tests

```bash
python -m pytest -q
```

37 tests across the deployment manifest, runner, evaluator, and cost
projection.

## Use your own deployments

The bundled `deployments/` directory is just examples. Point at your
own with `--deployments-dir`:

```bash
aoai-evals --deployments-dir path/to/your/manifests list
```

Or write your own manifest:

```json
{
  "name": "my-deployment",
  "model": "gpt-4o",
  "model_version": "2024-08-06",
  "region": "centralus",
  "capacity_tpm": 50000,
  "content_filter": {"hate": "medium", "sexual": "medium",
                     "violence": "medium", "self_harm": "medium",
                     "jailbreak_detection": true,
                     "protected_material_detection": true},
  "sla_p95_latency_ms": 1200,
  "monthly_budget_usd": 1000.0,
  "notes": "..."
}
```

Drop it in `deployments/my-deployment.json` and write a matching
`evals/my-deployment.json` with your eval cases.

## Wire to the real Azure OpenAI

1. `pip install -e ".[azure]"`
2. Set credentials:
   ```bash
   export AZURE_OPENAI_API_KEY=...
   export AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com
   export AOAI_BACKEND=azure
   ```
3. Implement `_call_azure` in
   [src/aoai_evals/runner.py](../src/aoai_evals/runner.py)
   per the docstring sketch (~15 lines).
4. Re-run `aoai-evals eval <deployment>` — now hits your real Azure
   endpoint.

Tests pin the backend to `stub` so they stay green while you wire
the live path.

## Next steps

- [Architecture](architecture.md) — deployment-readiness model
- [Customization](customization.md) — add deployments, rubrics, backends
- [Evaluation](evaluation.md) — eval rubrics + the readiness gate
