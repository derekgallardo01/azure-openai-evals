# Changelog

Notable changes to the Azure OpenAI evals kit. Dates are when the
change landed on `main`.

## 2026-06-28 — Initial public release (v1.0.0)
- `deployment.py` — typed `Deployment` + `ContentFilterConfig`; known
  pricing table + p50 latency baseline per model; validate() catches
  config issues before runtime
- `runner.py` — deterministic stub backend simulating Azure OpenAI's
  response shape (response_text + usage + content_filter result +
  latency); content-filter triggers honor per-deployment levels;
  Azure backend swap documented
- `evaluator.py` — six rubric types: 3 Azure-specific
  (`content_filter_safe`, `content_filter_blocks`, `latency_under`,
  `cost_under_per_call`, `json_schema_conforms`) + 3 generic
  (`contains_all`, `contains_any`, `in_set`); `project_monthly_cost`
  + `measure_latency` helpers
- `cli.py` — `list / show / validate / eval / cost / latency / demo`
  subcommands with `--json` machine-readable output
- 3 bundled deployments: gpt4o-eastus-prod, gpt4o-westeurope-failover,
  gpt4o-mini-bulk - covering the common multi-deployment pattern
  (primary + regional failover + cheap bulk classifier)
- 12 eval cases (4 per deployment) covering content filter, latency,
  cost; CI gates on 100% pass
- 37 pytest tests (deployment + runner + evaluator + cost projection
  + latency baseline)
- CI on Python 3.10/3.11/3.12; runs validate + eval + cost projection
  + latency baseline for every bundled deployment
- `pyproject.toml` with `[azure]` optional extra for `openai` SDK
- Docs trio: `getting-started`, `architecture`, `customization`,
  `evaluation`, `diagrams`, `faq`
- OSS niceties: `CONTRIBUTING`, `CODE_OF_CONDUCT`, `SECURITY`,
  `CITATION.cff`, `.editorconfig`, `.devcontainer/devcontainer.json`,
  `.github/ISSUE_TEMPLATE/*`, `.github/PULL_REQUEST_TEMPLATE.md`,
  `.github/dependabot.yml`
- `Dockerfile`, `pages.yml` (live demo: per-deployment card with
  config + eval pass/fail + cost projection + latency baseline),
  `screenshots.yml`, `portfolio.yml` — workflows include
  `git pull --rebase` before push (race-condition fix)
- README badges: CI + License (MIT) + Python (3.10+) + Open in
  Codespaces
- Theme: Microsoft blue (Azure)
