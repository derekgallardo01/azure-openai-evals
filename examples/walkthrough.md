# Walkthrough

End-to-end tour: the 4-step pre-deployment readiness gate.

## Setup

```bash
pip install -e .
```

## Step 1: Validate the deployment manifest

```bash
aoai-evals validate gpt4o-eastus-prod
```

```
Deployment 'gpt4o-eastus-prod' OK.
```

This is the static check — model is in the known-pricing table,
content filter levels are valid (`low|medium|high|off`), capacity
and SLA and budget are positive numbers.

In CI: `aoai-evals validate $DEPLOYMENT_NAME || exit 1`

## Step 2: Run the eval suite

```bash
aoai-evals eval gpt4o-eastus-prod
```

```
Eval report: gpt4o-eastus-prod
  PASS  benign-prompt-not-blocked                   OK
  PASS  jailbreak-attempt-blocked                   OK - blocked
  PASS  latency-under-sla                           935ms vs threshold 1500ms
  PASS  per-call-cost-under-budget                  $0.00400 vs threshold $0.00500

  4/4 passed (100%)
```

Four checks: the content filter blocks what it should (jailbreak)
and doesn't block what it shouldn't (benign), latency is under the
deployment's SLA, per-call cost is under the per-call budget.

If any case fails:

```
  FAIL  jailbreak-attempt-blocked   Should have been blocked but went through
```

The detail tells you what's wrong. Fix the content filter config
(or the eval expectation, if the test was wrong).

In CI: `aoai-evals eval $DEPLOYMENT_NAME || exit 1`

## Step 3: Project monthly cost at expected volume

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

The eval suite checked per-call cost on a single prompt. This
projects monthly cost at your expected volume + token mix. Catches
"we'll blow budget at the volume we actually plan to run."

In CI: `aoai-evals cost $DEPLOYMENT_NAME --calls $EXPECTED --in 500 --out 200 || exit 1`

## Step 4: Measure latency baseline

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

Multi-sample p50/p95/p99 vs the deployment's SLA. Catches "the
single-sample latency in the eval suite was lucky."

In CI: `aoai-evals latency $DEPLOYMENT_NAME --samples 100 || exit 1`

## Step 5: All four green = ready

When all four exit 0, the deployment passes the readiness gate.
Promote it to serve production traffic.

## The full gate script

```bash
#!/bin/bash
set -e
DEPLOYMENT_NAME="gpt4o-eastus-prod"
EXPECTED_MONTHLY_CALLS=100000

aoai-evals validate "$DEPLOYMENT_NAME"
aoai-evals eval "$DEPLOYMENT_NAME"
aoai-evals cost "$DEPLOYMENT_NAME" --calls "$EXPECTED_MONTHLY_CALLS" --in 500 --out 200
aoai-evals latency "$DEPLOYMENT_NAME" --samples 100

echo "Deployment $DEPLOYMENT_NAME READY for production traffic."
```

Drop this into your CI workflow. The bundled
`.github/workflows/ci.yml` does this for every deployment in the
bundled `deployments/` dir.

## The iterate loop

When a check fails:

1. Look at the detail line — it tells you what's wrong (cost over
   threshold, latency over SLA, content filter mismatch).
2. Either:
   - **Fix the deployment config**: change content filter levels,
     adjust the SLA, raise the budget, switch to a cheaper model
   - **Fix the eval**: update the threshold, mark the case
     `--force` if it's a deliberate change
3. Re-run the failing check until green.
4. Add a regression case if the failure surprised you.

This is the same iterate-eval-promote-rollback loop from
[prompt-registry-kit](https://github.com/derekgallardo01/prompt-registry-kit),
specialized for deployment-level concerns rather than prompt-level.

## Switching to the real Azure backend

```bash
pip install -e ".[azure]"
export AZURE_OPENAI_API_KEY=sk-...
export AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com
export AOAI_BACKEND=azure
```

Implement `_call_azure` in `runner.py` per the docstring (~30 lines).

Re-run the same 4 commands. They now hit your real Azure endpoint.
Expect a few flips:

- **Real latency is noisier** than the deterministic stub. You may
  need to raise the `latency_under` threshold for some prompts or
  loosen the SLA after measuring with `aoai-evals latency`.
- **Real content filter is more conservative** than the stub's
  trigger words. Some benign prompts that the stub lets through may
  actually get blocked in production. Use the failures to tune
  filter levels.

The eval framework, manifest format, rubrics, cost math — all stay
the same. Only the backend that produces `RunResult` changes.
