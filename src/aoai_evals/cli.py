"""CLI - list deployments, run evals, project costs, measure latency.

Usage:
    aoai-evals list                                  # all deployments + key config
    aoai-evals show <deployment>                     # full config for one deployment
    aoai-evals validate <deployment>                 # static check (config shape, model known, etc.)
    aoai-evals eval <deployment>                     # run the eval suite
    aoai-evals cost <deployment> --calls 10000 --in 500 --out 200
    aoai-evals latency <deployment> [--samples 20]
    aoai-evals demo                                  # eval every bundled deployment
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from .deployment import Deployment, list_deployments, load_deployment, default_deployments_dir
from .evaluator import evaluate_deployment, measure_latency, project_monthly_cost
from .runner import Runner


def _deployments_dir(args) -> Path:
    return Path(args.deployments_dir) if args.deployments_dir else default_deployments_dir()


def _evals_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "evals"


def _load_named_deployment(args) -> Deployment:
    d_dir = _deployments_dir(args)
    return load_deployment(d_dir / f"{args.deployment}.json")


def _load_eval_cases(deployment_name: str) -> list[dict]:
    eval_file = _evals_dir() / f"{deployment_name}.json"
    if not eval_file.exists():
        raise FileNotFoundError(
            f"No eval cases at {eval_file}. Add evals/{deployment_name}.json."
        )
    with open(eval_file) as f:
        return json.load(f)["cases"]


def cmd_list(args) -> int:
    d_dir = _deployments_dir(args)
    deployments = list_deployments(d_dir)
    if not deployments:
        print(f"No deployments found in {d_dir}.")
        return 0
    print(f"Deployments in {d_dir}\n")
    for d in deployments:
        print(f"  {d.name:35s}  {d.model:20s}  {d.region:12s}  "
              f"capacity {d.capacity_tpm} tpm  budget ${d.monthly_budget_usd:.0f}/mo")
    return 0


def cmd_show(args) -> int:
    d = _load_named_deployment(args)
    print(f"Deployment: {d.name}")
    print(f"  model:              {d.model}  (version {d.model_version})")
    print(f"  region:             {d.region}")
    print(f"  capacity_tpm:       {d.capacity_tpm}")
    print(f"  sla_p95_latency:    {d.sla_p95_latency_ms}ms")
    print(f"  monthly_budget:     ${d.monthly_budget_usd:.2f}")
    print(f"  content filter:")
    cf = d.content_filter
    print(f"    hate / sexual / violence / self_harm: "
          f"{cf.hate} / {cf.sexual} / {cf.violence} / {cf.self_harm}")
    print(f"    jailbreak detection:           {cf.jailbreak_detection}")
    print(f"    protected material detection:  {cf.protected_material_detection}")
    if d.notes:
        print(f"  notes: {d.notes}")
    return 0


def cmd_validate(args) -> int:
    d = _load_named_deployment(args)
    problems = d.validate()
    if problems:
        print(f"Deployment '{d.name}' has {len(problems)} problem(s):")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(f"Deployment '{d.name}' OK.")
    return 0


def cmd_eval(args) -> int:
    d = _load_named_deployment(args)
    cases = _load_eval_cases(d.name)
    report = evaluate_deployment(d, cases)
    if args.json:
        out = {"deployment": d.name,
               "cases": [asdict(c) for c in report.cases],
               "pass_rate": report.pass_rate}
        print(json.dumps(out, indent=2))
    else:
        print(f"\nEval report: {d.name}")
        for c in report.cases:
            status = "PASS" if c.passed else "FAIL"
            print(f"  {status}  {c.case_id:40s}  {c.detail}")
        print(f"\n  {report.passed}/{report.total} passed ({report.pass_rate:.0%})")
    return 0 if report.passed == report.total else 1


def cmd_cost(args) -> int:
    d = _load_named_deployment(args)
    proj = project_monthly_cost(
        d, monthly_calls=args.calls,
        avg_input_tokens=args.in_tokens, avg_output_tokens=args.out_tokens,
    )
    if args.json:
        print(json.dumps(asdict(proj), indent=2))
    else:
        print(f"\nMonthly cost projection: {d.name}")
        print(f"  model:           {d.model}")
        print(f"  monthly calls:   {proj.monthly_calls:,}")
        print(f"  avg tokens:      {proj.avg_input_tokens} in, {proj.avg_output_tokens} out")
        print(f"  monthly cost:    ${proj.monthly_cost_usd:,.2f}")
        print(f"  monthly budget:  ${proj.budget_usd:,.2f}")
        if proj.over_budget:
            overage = proj.monthly_cost_usd - proj.budget_usd
            print(f"  OVER BUDGET by ${overage:,.2f}")
            return 1
        print(f"  under budget by ${proj.budget_usd - proj.monthly_cost_usd:,.2f}")
    return 0


def cmd_latency(args) -> int:
    d = _load_named_deployment(args)
    report = measure_latency(
        d, prompt=args.prompt or "Translate 'Hello, world' into Spanish.",
        samples=args.samples,
    )
    if args.json:
        print(json.dumps(asdict(report), indent=2))
    else:
        print(f"\nLatency baseline: {d.name}  ({report.samples} samples)")
        print(f"  p50:  {report.p50_ms}ms")
        print(f"  p95:  {report.p95_ms}ms")
        print(f"  p99:  {report.p99_ms}ms")
        print(f"  SLA (p95): {report.sla_p95_ms}ms - "
              f"{'OK' if report.p95_under_sla else 'OVER SLA'}")
    return 0 if report.p95_under_sla else 1


def cmd_demo(args) -> int:
    d_dir = _deployments_dir(args)
    deployments = list_deployments(d_dir)
    overall_ok = True
    for d in deployments:
        cases = _load_eval_cases(d.name)
        report = evaluate_deployment(d, cases)
        mark = "[OK]" if report.passed == report.total else "[FAIL]"
        print(f"  {mark} {d.name:35s}  {report.passed}/{report.total} cases passed  "
              f"({report.pass_rate:.0%})")
        if report.passed != report.total:
            overall_ok = False
            for c in report.cases:
                if not c.passed:
                    print(f"      FAIL  {c.case_id}: {c.detail}")
    print(f"\n  Backend: {Runner().backend}")
    return 0 if overall_ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Azure OpenAI deployment readiness + eval CLI.")
    parser.add_argument("--deployments-dir", default=None,
                        help="Path to deployments root (default: bundled deployments/)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list")

    p_show = sub.add_parser("show"); p_show.add_argument("deployment")
    p_val = sub.add_parser("validate"); p_val.add_argument("deployment")

    p_eval = sub.add_parser("eval")
    p_eval.add_argument("deployment")
    p_eval.add_argument("--json", action="store_true")

    p_cost = sub.add_parser("cost")
    p_cost.add_argument("deployment")
    p_cost.add_argument("--calls", type=int, default=10000)
    p_cost.add_argument("--in", type=int, default=500, dest="in_tokens")
    p_cost.add_argument("--out", type=int, default=200, dest="out_tokens")
    p_cost.add_argument("--json", action="store_true")

    p_lat = sub.add_parser("latency")
    p_lat.add_argument("deployment")
    p_lat.add_argument("--samples", type=int, default=10)
    p_lat.add_argument("--prompt", default=None)
    p_lat.add_argument("--json", action="store_true")

    sub.add_parser("demo")

    args = parser.parse_args(argv)
    handlers = {"list": cmd_list, "show": cmd_show, "validate": cmd_validate,
                "eval": cmd_eval, "cost": cmd_cost, "latency": cmd_latency,
                "demo": cmd_demo}
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
