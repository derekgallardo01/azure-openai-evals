"""Full pre-deployment readiness gate for one or more AOAI deployments.

The "is this deployment ready for production traffic?" workflow as a single
script. Runs the 4-step gate against every deployment in the bundled
manifest:

  1. validate() — static config check
  2. eval suite — content-filter + latency + cost rubrics
  3. cost projection at expected volume
  4. latency baseline measurement (N samples)

Exit code 0 only if ALL deployments pass ALL 4 checks. Wire into CI
to gate merges that touch deployments/ manifests, or into a release
workflow before promoting a new deployment to serve traffic.

Usage:
    python examples/preflight_gate.py
    python examples/preflight_gate.py --calls 50000 --in 500 --out 200 --samples 50
    python examples/preflight_gate.py --only gpt4o-eastus-prod --json
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aoai_evals.deployment import (  # noqa: E402
    Deployment, list_deployments, default_deployments_dir,
)
from aoai_evals.evaluator import (  # noqa: E402
    evaluate_deployment, project_monthly_cost, measure_latency,
)


def gate_one(deployment: Deployment, eval_cases: list[dict],
              expected_monthly_calls: int, avg_input_tokens: int,
              avg_output_tokens: int, latency_samples: int) -> dict:
    """Run all 4 readiness checks against one deployment.

    Returns: {
        deployment, all_passed,
        validate: {ok, problems},
        eval: {passed, total, failed_cases},
        cost: {projected_usd, budget_usd, over_budget},
        latency: {p95_ms, sla_ms, p95_under_sla}
    }
    """
    result = {
        "deployment": deployment.name,
        "all_passed": True,
    }

    # 1. validate()
    problems = deployment.validate()
    result["validate"] = {"ok": not problems, "problems": problems}
    if problems:
        result["all_passed"] = False

    # 2. eval suite
    if eval_cases:
        eval_report = evaluate_deployment(deployment, eval_cases)
        failed = [{"id": c.case_id, "detail": c.detail}
                   for c in eval_report.cases if not c.passed]
        result["eval"] = {
            "passed": eval_report.passed,
            "total": eval_report.total,
            "failed_cases": failed,
        }
        if eval_report.passed != eval_report.total:
            result["all_passed"] = False
    else:
        result["eval"] = {"passed": 0, "total": 0, "skipped": "no_cases"}

    # 3. cost projection
    cost = project_monthly_cost(
        deployment, monthly_calls=expected_monthly_calls,
        avg_input_tokens=avg_input_tokens, avg_output_tokens=avg_output_tokens,
    )
    result["cost"] = {
        "projected_usd": cost.monthly_cost_usd,
        "budget_usd": cost.budget_usd,
        "over_budget": cost.over_budget,
    }
    if cost.over_budget:
        result["all_passed"] = False

    # 4. latency baseline
    latency = measure_latency(deployment, prompt="Hello, world.",
                               samples=latency_samples)
    result["latency"] = {
        "p50_ms": latency.p50_ms,
        "p95_ms": latency.p95_ms,
        "p99_ms": latency.p99_ms,
        "sla_p95_ms": latency.sla_p95_ms,
        "p95_under_sla": latency.p95_under_sla,
    }
    if not latency.p95_under_sla:
        result["all_passed"] = False

    return result


def load_eval_cases_for(name: str) -> list[dict]:
    """Load the bundled eval cases for a deployment, if present."""
    eval_file = Path(__file__).resolve().parents[1] / "evals" / f"{name}.json"
    if not eval_file.exists():
        return []
    with open(eval_file) as f:
        return json.load(f)["cases"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AOAI pre-deployment readiness gate.")
    parser.add_argument("--deployments-dir", default=None)
    parser.add_argument("--only", default=None,
                        help="Run the gate against this deployment only.")
    parser.add_argument("--calls", type=int, default=10000,
                        help="Expected monthly calls for cost projection.")
    parser.add_argument("--in", type=int, default=500, dest="in_tokens")
    parser.add_argument("--out", type=int, default=200, dest="out_tokens")
    parser.add_argument("--samples", type=int, default=20,
                        help="Latency baseline sample count.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    d_dir = Path(args.deployments_dir) if args.deployments_dir else default_deployments_dir()
    deployments = list_deployments(d_dir)
    if args.only:
        deployments = [d for d in deployments if d.name == args.only]
        if not deployments:
            print(f"No deployment matching '{args.only}'", file=sys.stderr)
            return 1

    results = []
    for d in deployments:
        cases = load_eval_cases_for(d.name)
        result = gate_one(
            d, cases,
            expected_monthly_calls=args.calls,
            avg_input_tokens=args.in_tokens,
            avg_output_tokens=args.out_tokens,
            latency_samples=args.samples,
        )
        results.append(result)

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print(f"\n{'='*70}\nPre-flight readiness gate ({len(results)} deployments)\n{'='*70}\n")
        for r in results:
            status = "READY" if r["all_passed"] else "BLOCKED"
            print(f"  [{status:7s}] {r['deployment']}")
            v = r["validate"]
            print(f"    validate:  {'OK' if v['ok'] else 'FAIL'}"
                  + (f" — {v['problems']}" if not v['ok'] else ""))
            e = r["eval"]
            if e.get("skipped"):
                print(f"    eval:      SKIP ({e['skipped']})")
            else:
                print(f"    eval:      {e['passed']}/{e['total']} cases passed")
                for f in e.get("failed_cases", []):
                    print(f"               FAIL  {f['id']}: {f['detail']}")
            c = r["cost"]
            cost_status = "OVER BUDGET" if c["over_budget"] else "OK"
            print(f"    cost:      {cost_status} — ${c['projected_usd']:.2f}/mo "
                  f"vs ${c['budget_usd']:.2f} budget at {args.calls:,} calls")
            l = r["latency"]
            lat_status = "OK" if l["p95_under_sla"] else "OVER SLA"
            print(f"    latency:   {lat_status} — p95 {l['p95_ms']}ms vs SLA {l['sla_p95_ms']}ms\n")

        all_ok = all(r["all_passed"] for r in results)
        print(f"\n  Overall: {'ALL READY' if all_ok else 'SOME BLOCKED'}")

    # Exit non-zero if any deployment failed
    return 0 if all(r["all_passed"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
