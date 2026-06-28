# Customization

How to shape the kit for your Azure environment.

## Add a deployment

Write a JSON file in `deployments/`:

```json
{
  "name": "gpt4o-mini-japan",
  "model": "gpt-4o-mini",
  "model_version": "2024-07-18",
  "region": "japaneast",
  "capacity_tpm": 50000,
  "content_filter": {
    "hate": "medium",
    "sexual": "medium",
    "violence": "medium",
    "self_harm": "medium",
    "jailbreak_detection": true,
    "protected_material_detection": true
  },
  "sla_p95_latency_ms": 900,
  "monthly_budget_usd": 150.0,
  "notes": "Japan region for APAC customer."
}
```

Validate: `aoai-evals validate gpt4o-mini-japan`. Add an eval file at
`evals/gpt4o-mini-japan.json`. That's it — the CLI, demo, and tests
pick it up automatically.

## Add a custom rubric type

Edit `src/aoai_evals/evaluator.py::evaluate_case`. Add a branch:

```python
if rubric_type == "p99_under":
    threshold = int(rubric["p99_under"])
    # Requires measuring p99 separately - the per-case run only has
    # one latency sample. Use measure_latency() for true p99.
    raise NotImplementedError("Use aoai-evals latency for p99 checks")
```

Or for response-content checks that don't fit the existing rubrics:

```python
if rubric_type == "no_pii":
    # Reject responses that contain SSN, credit card patterns, etc.
    import re
    SSN = re.compile(r"\d{3}-\d{2}-\d{4}")
    CC = re.compile(r"\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}")
    leaked = []
    if SSN.search(run.response_text): leaked.append("SSN")
    if CC.search(run.response_text): leaked.append("credit-card")
    ok = not leaked
    return CaseResult(case_id=case["id"], passed=ok, expected=rubric,
                      detail="OK" if ok else f"Leaked: {leaked}")
```

Reference it in your eval JSON:

```json
{"id": "no-pii-leak", "prompt": "...",
 "expected": {"no_pii": true}}
```

## Add a custom content-filter trigger

The stub backend's content-filter triggers are in
`runner.py::_content_filter_triggers`. To simulate a new category:

```python
def _content_filter_triggers(cf) -> dict[str, list[str]]:
    triggers = {...}
    if cf.protected_material_detection:
        triggers["protected_material"] = [
            "lyrics to ", "complete chapter of",
        ]
    return triggers
```

In the real Azure backend, these triggers happen server-side. The
stub fakes them so the `content_filter_blocks` rubric has a
positive-case eval.

## Update the pricing table

`KNOWN_PRICING` in `deployment.py` is a snapshot. Update when prices
change:

```python
KNOWN_PRICING: dict[str, dict[str, float]] = {
    "gpt-4o":      {"input": 0.005,  "output": 0.015},
    "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
    # ... add new entries
}
```

For dynamic pricing (per-region surcharge, committed-use discounts),
make `KNOWN_PRICING` a function that takes the deployment:

```python
def pricing_for(deployment) -> dict[str, float]:
    base = KNOWN_PRICING.get(deployment.model, ...)
    if deployment.region.startswith("eastus") or deployment.region.startswith("westus"):
        return base
    # Some regions have a surcharge
    return {k: v * 1.1 for k, v in base.items()}
```

Then `Deployment.cost_estimate` calls `pricing_for(self)` instead of
indexing `KNOWN_PRICING` directly.

## Wire the real Azure backend

`runner.py::_call_azure` is the seam:

```python
def _call_azure(self, deployment, prompt, expected_output_tokens):
    from openai import AzureOpenAI
    client = AzureOpenAI(
        api_key=os.environ["AZURE_OPENAI_API_KEY"],
        api_version="2024-08-01-preview",
        azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
    )
    try:
        response = client.chat.completions.create(
            model=deployment.name,  # NOTE: deployment name, not model id
            messages=[{"role": "user", "content": prompt}],
            max_tokens=expected_output_tokens,
        )
        choice = response.choices[0]
        blocked = choice.finish_reason == "content_filter"
        triggered = []
        if blocked and hasattr(choice, "content_filter_results"):
            for category, result in choice.content_filter_results.items():
                if result.get("filtered"):
                    triggered.append(category)

        return RunResult(
            deployment_name=deployment.name,
            model=deployment.model,
            region=deployment.region,
            prompt=prompt,
            response_text=choice.message.content or "",
            input_tokens=response.usage.prompt_tokens,
            output_tokens=response.usage.completion_tokens,
            latency_ms=0,  # let run() use wall-clock latency
            content_filter=ContentFilterResult(blocked=blocked, triggered_categories=triggered),
            estimated_cost_usd=0.0,  # filled in by run()
            backend="azure",
        )
    except Exception as ex:
        # Content-filter errors come through as APIError with specific code
        if "content_filter" in str(ex).lower():
            return RunResult(..., content_filter=ContentFilterResult(blocked=True, ...))
        raise
```

About 30 lines total. Done.

## Use against Azure AI Foundry (not classic Azure OpenAI)

Azure AI Foundry serves models beyond OpenAI (Claude, Llama, etc.).
Same shape, different endpoint:

```python
client = AzureOpenAI(
    api_key=os.environ["AZURE_AI_FOUNDRY_API_KEY"],
    api_version="2024-05-01-preview",
    azure_endpoint=os.environ["AZURE_AI_FOUNDRY_ENDPOINT"],
)
# The deployment name format is different on Foundry; check your model card.
```

Pricing differs by model (Claude on Foundry vs Anthropic direct). Add
the Foundry models to `KNOWN_PRICING`.

## Run evals on a schedule

Wire a cron job that runs `aoai-evals demo` daily and pages on
failures:

```yaml
# .github/workflows/daily-evals.yml
name: Daily Azure deployment evals
on:
  schedule:
    - cron: '0 9 * * *'  # 9am UTC daily
jobs:
  evals:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: {python-version: "3.11"}
      - run: pip install -e ".[azure]"
      - run: aoai-evals demo
        env:
          AOAI_BACKEND: azure
          AZURE_OPENAI_API_KEY: ${{ secrets.AZURE_OPENAI_API_KEY }}
          AZURE_OPENAI_ENDPOINT: ${{ secrets.AZURE_OPENAI_ENDPOINT }}
      - name: Notify on failure
        if: failure()
        run: gh issue create --title "AOAI deployment eval failed" --body "See run logs"
```

This catches Azure-side drift: content filter updates, latency
regressions during regional incidents, pricing changes that break the
budget gate.

## Track results over time

The kit emits a `RunResult` per eval case. To track over time, save
each result with a timestamp:

```python
import json, time, sqlite3
from dataclasses import asdict

conn = sqlite3.connect("evals.db")
conn.execute("""CREATE TABLE IF NOT EXISTS runs
                (ts INTEGER, deployment TEXT, case_id TEXT,
                 passed INTEGER, detail TEXT, latency_ms INTEGER, cost_usd REAL)""")

for case in cases:
    run = runner.run(d, case["prompt"])
    result = evaluate_case(case, run, d)
    conn.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?)",
                 (int(time.time()), d.name, case["id"],
                  int(result.passed), result.detail,
                  run.latency_ms, run.estimated_cost_usd))
conn.commit()
```

Then query for "latency p95 over the last 7 days per deployment" or
"how often did the content filter block in the last month."
