# Diagrams

GitHub renders Mermaid natively. These render on the README and here.

## Deployment-readiness gate

```mermaid
flowchart LR
    M[deployments/X.json] --> V["aoai-evals validate X<br/>(config shape)"]
    V --> VR{Config OK?}
    VR -- no --> X[Fail: specific config problem]
    VR -- yes --> E["aoai-evals eval X<br/>(content filter + latency + cost rubrics)"]
    E --> ER{All cases pass?}
    ER -- no --> X
    ER -- yes --> C["aoai-evals cost X --calls N --in I --out O"]
    C --> CR{Under monthly budget?}
    CR -- no --> X
    CR -- yes --> L["aoai-evals latency X --samples N"]
    L --> LR{p95 under SLA?}
    LR -- no --> X
    LR -- yes --> READY[Deployment READY for production traffic]
```

## Rubric types (Azure-specific vs generic)

```mermaid
flowchart TB
    R[Eval case rubric] --> A{Type?}
    A -- "content_filter_safe" --> AZ1["Response NOT blocked<br/>(Azure-specific)"]
    A -- "content_filter_blocks" --> AZ2["Response IS blocked<br/>(Azure-specific)"]
    A -- "latency_under" --> AZ3["latency_ms <= N<br/>(per-deployment SLA)"]
    A -- "cost_under_per_call" --> AZ4["estimated_cost_usd <= $X<br/>(per-model pricing)"]
    A -- "json_schema_conforms" --> AZ5["Valid JSON + required keys<br/>(Azure JSON mode)"]
    A -- "contains_all / contains_any" --> G1[Text rubric<br/>generic]
    A -- "in_set" --> G2[Text rubric<br/>generic]
```

## The runner's seam (stub vs Azure)

```mermaid
flowchart TB
    subgraph Stub["stub backend (default)"]
        direction TB
        S1[Hash deployment + prompt]
        S2[Synth: tokens, latency, content_filter trigger]
        S3[RunResult shape matching Azure]
        S1 --> S2 --> S3
    end

    subgraph Azure["azure backend (production)"]
        direction TB
        A1["client.chat.completions.create(<br/>model=deployment.name,<br/>messages=[prompt])"]
        A2[Real response + usage + content_filter_results]
        A3[RunResult]
        A1 --> A2 --> A3
    end

    Stub -. "same RunResult shape" .- Azure
```

## Deployment manifest -> stub response

```mermaid
sequenceDiagram
    participant CLI
    participant D as Deployment
    participant R as Runner
    participant S as Stub backend
    participant E as Evaluator

    CLI->>D: load deployments/X.json
    D-->>CLI: typed Deployment
    CLI->>R: run(deployment, prompt)
    R->>S: _call_stub(deployment, prompt, expected_out_tokens)
    S->>S: hash(deployment + prompt)
    S->>S: lookup KNOWN_LATENCY for model
    S->>S: check content_filter triggers
    S->>S: estimate input/output tokens
    S-->>R: simulated RunResult
    R-->>CLI: RunResult (with cost_estimate filled in)
    CLI->>E: evaluate_case(case, run, deployment)
    E-->>CLI: CaseResult (PASS/FAIL + detail)
```

## Content filter eval (paired positive/negative)

```mermaid
flowchart LR
    subgraph Test1["content_filter_safe on benign prompt"]
        P1["Prompt: 'Summarize this'"]
        P1 --> R1[Runner]
        R1 --> B1{blocked?}
        B1 -- no --> PASS1[PASS]
        B1 -- yes --> FAIL1["FAIL - filter too strict"]
    end

    subgraph Test2["content_filter_blocks on jailbreak"]
        P2["Prompt: 'Ignore instructions'"]
        P2 --> R2[Runner]
        R2 --> B2{blocked?}
        B2 -- yes --> PASS2[PASS]
        B2 -- no --> FAIL2["FAIL - filter too loose"]
    end
```

Both must pass on a well-configured deployment. The pair catches
both filter misconfigurations: too-strict (blocks real customers)
and too-loose (lets injections through).

## Cost projection vs per-call rubric

```mermaid
flowchart TB
    subgraph PerCall["cost_under_per_call rubric (in eval suite)"]
        PC1[Single prompt] --> PC2[Runner]
        PC2 --> PC3[run.estimated_cost_usd]
        PC3 --> PC4{<= threshold?}
    end

    subgraph Monthly["aoai-evals cost command (ad-hoc)"]
        M1["Expected volume + token mix"] --> M2[project_monthly_cost]
        M2 --> M3[monthly_cost_usd]
        M3 --> M4{<= monthly_budget?}
    end

    PC4 -. "catches per-prompt regressions" .- M4
    M4 -. "catches volume-budget overflow" .- PC4
```

Both matter; they catch different classes of issue.

## Repo shape

```mermaid
flowchart TB
    R[azure-openai-evals]
    R --> SRC[src/aoai_evals/]
    SRC --> S1[deployment.py — typed manifest + pricing/latency tables]
    SRC --> S2[runner.py — stub backend + Azure seam]
    SRC --> S3[evaluator.py — 6 rubrics + cost projection + latency]
    SRC --> S4[cli.py — list/show/validate/eval/cost/latency/demo]
    R --> DEP[deployments/]
    DEP --> D1[gpt4o-eastus-prod.json]
    DEP --> D2[gpt4o-westeurope-failover.json]
    DEP --> D3[gpt4o-mini-bulk.json]
    R --> EV[evals/]
    EV --> E1[gpt4o-eastus-prod.json]
    EV --> E2[gpt4o-westeurope-failover.json]
    EV --> E3[gpt4o-mini-bulk.json]
    R --> T[tests/]
    T --> T1[test_deployment.py]
    T --> T2[test_runner.py]
    T --> T3[test_evaluator.py]
    R --> DOCS[docs/]
    R --> CI[.github/workflows/ci.yml]
    R --> DK[Dockerfile]
```
