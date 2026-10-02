"""Run the probe and forecast tasks for every model and write JSONL records."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import RESULTS_DIR, agents, probe
from .llm import BudgetExceeded, Client

# BLS round being forecast: fieldwork in late September 2026, published 27 Oct 2026.
BLS_ROUND = {"quarter_label": "October 2026", "past_window": "July to September 2026",
             "next_window": "October to December 2026"}
HOUSEHOLD_ARMS = ("minimal", "news")


def job_list(cfg: dict, archetypes: dict, snapshots: dict, tasks: list[str]) -> list[dict]:
    s = cfg["sampling"]
    jobs = []
    for model in cfg["models"]:
        m = model["id"]
        if "probe" in tasks:
            for qid, question, expected, kind in probe.PROBE_QUESTIONS:
                for i in range(s["probe_samples"]):
                    jobs.append({"task": "probe", "model": m, "unit": qid, "sample": i, "expected": expected, "kind": kind,
                                 "messages": probe.probe_messages(question), "schema": probe.probe_schema(),
                                 "temperature": 0.0 if i == 0 else s["temperature"], "max_tokens": 400})
        if "bls" in tasks:
            info = snapshots["bls_2026_q3"]
            for bank in agents.banks(archetypes):
                messages = agents.bank_messages(bank, info, **BLS_ROUND)
                for i in range(s["bank_samples"]):
                    jobs.append({"task": "bls", "model": m, "unit": bank.id, "weight": bank.weight, "sample": i,
                                 "messages": messages, "schema": agents.bank_schema(),
                                 "temperature": s["temperature"], "max_tokens": s["max_tokens"]})
        if "ces" in tasks:
            info = snapshots["ces_2026_09"]
            for hh in agents.households(archetypes, cfg["targets"]["ces"]["countries"]):
                for arm in HOUSEHOLD_ARMS:
                    messages = agents.household_messages(hh, info, arm)
                    for i in range(s["household_samples"]):
                        jobs.append({"task": "ces", "model": m, "unit": hh.id, "country": hh.country, "arm": arm,
                                     "weight": hh.weight, "sample": i, "messages": messages,
                                     "schema": agents.household_schema(),
                                     "temperature": s["temperature"], "max_tokens": s["max_tokens"]})
    return jobs


def estimate_cost(client: Client, jobs: list[dict]) -> dict[str, float]:
    out: dict[str, float] = {}
    for j in jobs:
        out[j["model"]] = out.get(j["model"], 0.0) + client.estimate(j["model"], j["messages"], j["max_tokens"])
    return out


def _run_one(client: Client, job: dict) -> dict:
    record = {k: v for k, v in job.items() if k not in ("messages", "schema")}
    try:
        c = client.complete(job["model"], job["messages"], schema=job["schema"], temperature=job["temperature"],
                            max_tokens=job["max_tokens"], sample=job["sample"])
        record.update(request_hash=c.request_hash, served_model=c.model, cached=c.cached, cost_usd=c.cost_usd,
                      usage=c.usage, raw_content=c.content)
        record["answer"] = c.json()
    except BudgetExceeded:
        raise
    except Exception as e:  # keep going; failures are reported, not silently dropped
        record["error"] = f"{type(e).__name__}: {e}"
    return record


def run_jobs(client: Client, jobs: list[dict], out_dir: Path, workers: int = 8) -> dict[str, int]:
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {}
    counts: dict[str, int] = {}
    try:
        with ThreadPoolExecutor(workers) as pool:
            for record in pool.map(lambda j: _run_one(client, j), jobs):
                task = record["task"]
                if task not in files:
                    files[task] = open(out_dir / f"{task}.jsonl", "w")
                files[task].write(json.dumps(record) + "\n")
                key = f"{task}:{'error' if 'error' in record else 'ok'}"
                counts[key] = counts.get(key, 0) + 1
    finally:
        for f in files.values():
            f.close()
    return counts


def results_dir(cfg: dict, dry_run: bool) -> Path:
    return RESULTS_DIR / (cfg["experiment_id"] + ("-dryrun" if dry_run else ""))
