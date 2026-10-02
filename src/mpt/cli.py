"""Command line entry point: python -m mpt.cli <command>."""

from __future__ import annotations

import argparse
import json
import sys

import yaml

from . import CONFIG_DIR, RESULTS_DIR, evaluate, run, snapshot
from .llm import Budget, BudgetExceeded, Client, CreditsExhausted, fetch_pricing

TASKS = ["probe", "bls", "ces"]


def load_config() -> tuple[dict, dict]:
    cfg = yaml.safe_load((CONFIG_DIR / "experiment.yaml").read_text())
    archetypes = yaml.safe_load((CONFIG_DIR / "archetypes.yaml").read_text())
    return cfg, archetypes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mpt")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("data", help="download raw data and build point-in-time information sets")
    p.add_argument("--refresh", action="store_true", help="re-download even if data/raw.json exists")

    for name, help_text in [("estimate", "print the number of calls and worst-case cost, without calling any model"),
                            ("run", "call the models and write results/<experiment>/<task>.jsonl")]:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--tasks", nargs="+", choices=TASKS, default=TASKS)
        p.add_argument("--models", nargs="+", help="subset of model ids from config/experiment.yaml")
        if name == "run":
            p.add_argument("--dry-run", action="store_true", help="no network; placeholder answers")
            p.add_argument("--workers", type=int, default=8)
            p.add_argument("--budget", type=float, help="override budget_usd from the config")

    sub.add_parser("baselines", help="fetch the latest published value of every target series")
    p = sub.add_parser("summary", help="print aggregated forecasts")
    p.add_argument("--dry-run", action="store_true")
    sub.add_parser("freeze", help="write predictions.json and MANIFEST.sha256 before the data release")
    sub.add_parser("score", help="after the release: compare frozen forecasts with actual data")

    args = parser.parse_args(argv)
    cfg, archetypes = load_config()

    if args.command == "data":
        snaps = snapshot.build_snapshots(cfg, refresh=args.refresh)
        for name, info in snaps.items():
            print(f"{name}: as of {info['asof']}, {len(info['series'])} series, statements {list(info['statements'])}")
        return 0

    if args.command in ("estimate", "run"):
        if args.models:
            unknown = set(args.models) - {m["id"] for m in cfg["models"]}
            if unknown:
                parser.error(f"models not in config: {sorted(unknown)}")
            cfg["models"] = [m for m in cfg["models"] if m["id"] in args.models]
        snaps = {name: snapshot.load_snapshot(name) for name in cfg["information_sets"]}
        jobs = run.job_list(cfg, archetypes, snaps, args.tasks)
        dry_run = args.command == "estimate" or args.dry_run
        budget = Budget(getattr(args, "budget", None) or cfg["budget_usd"])
        # Pricing comes from OpenRouter's public model list; a dry run needs none.
        pricing = fetch_pricing() if args.command == "estimate" or not dry_run else {}
        client = Client(budget, dry_run=dry_run, pricing=pricing)
        if args.command == "estimate":
            costs = run.estimate_cost(client, jobs)
            for model, cost in costs.items():
                n = sum(j["model"] == model for j in jobs)
                print(f"{model:40s} {n:6d} calls   worst case ${cost:7.3f}")
            print(f"{'total':40s} {len(jobs):6d} calls   worst case ${sum(costs.values()):7.3f}   budget ${budget.limit_usd:.2f}")
            return 0
        out_dir = run.results_dir(cfg, dry_run)
        try:
            counts = run.run_jobs(client, jobs, out_dir, workers=args.workers)
        except (BudgetExceeded, CreditsExhausted) as e:
            print(f"stopped: {e}", file=sys.stderr)
            return 2
        print(json.dumps(counts, indent=1))
        print(f"spent ${budget.spent_usd:.4f}; results in {out_dir}")
        return 0

    if args.command == "baselines":
        print(json.dumps(evaluate.build_baselines(cfg), indent=1))
        return 0

    out_dir = run.results_dir(cfg, getattr(args, "dry_run", False))
    if args.command == "summary":
        print(json.dumps(evaluate.summarise(out_dir), indent=1))
    elif args.command == "freeze":
        path = evaluate.freeze(cfg, out_dir)
        print(f"wrote {path}\n{(out_dir / 'MANIFEST.sha256').read_text()}")
        print(f"predictions.json sha256: {evaluate.sha256(path)}")
    elif args.command == "score":
        print(json.dumps(evaluate.score(RESULTS_DIR / cfg["experiment_id"] / "predictions.json"), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
